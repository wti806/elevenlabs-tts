import grpc
import sys
import queue
import threading
import time
import sounddevice as sd
import elevenlabs_pb2
import elevenlabs_pb2_grpc
import argparse

SAMPLE_RATE = 24000
DTYPE = 'int16'
CHANNELS = 1

_SERVER_HOSTNAME = 'elevenlabs-1006159354920.us-central1.run.app'
_SERVER_PORT = 443
_TARGET_ADDRESS = f'{_SERVER_HOSTNAME}:{_SERVER_PORT}'


def generate_requests(voice_id: str, model_id: str):
    """Generator function to yield requests to the gRPC server."""
    # Send Config message first
    print(f"\n[Request] Sending Config: voice='{voice_id}', model='{model_id}'")
    config = elevenlabs_pb2.Config(voice_id=voice_id, model_id=model_id, output_format="ulaw_8000")
    yield elevenlabs_pb2.StreamingSynthesizeRequest(config=config)

    print("Enter text to synthesize line by line.")
    print("Press Enter on an empty line when finished.")

    while True:
        try:
            text = input("> ")
        except EOFError: # Handle Ctrl+D or piped input ending
             print("\n[Request] End of input detected.")
             break
        if not text:
            print("[Request] Input stream finished by user. Half close the stream.")
            break

        print(f"[Request]   Sending Input: '{text}'")
        input_msg = elevenlabs_pb2.Input(text=text)
        yield elevenlabs_pb2.StreamingSynthesizeRequest(input=input_msg)


# --- Audio Playback Thread ---
def play_audio_thread(audio_queue: queue.Queue, stop_event: threading.Event):
    """Thread function to play audio chunks from a queue."""
    stream = None
    try:
        print(f"\n[Audio] Initializing playback stream (Format: {SAMPLE_RATE} Hz, {DTYPE}, {CHANNELS}ch)...")
        stream = sd.RawOutputStream(
            samplerate=SAMPLE_RATE,
            dtype=DTYPE,
            channels=CHANNELS,
            blocksize=2048
        )
        stream.start()
        print("[Audio] Playback stream started. Waiting for data...")

        while not stop_event.is_set():
            try:
                chunk = audio_queue.get(timeout=0.1)
                if chunk is None:
                    print("[Audio] End of stream signal received by player.")
                    break
                if chunk:
                    stream.write(chunk)
                if chunk is not None:
                     audio_queue.task_done()
            except queue.Empty:
                continue
            except Exception as e:
                 print(f"\n[Audio] Error writing to audio stream: {e}", file=sys.stderr)

        if stream and stream.active and not stop_event.is_set():
            print("[Audio] Waiting for final audio buffer to clear...")
            sleep_duration_ms = int((stream.latency * 1000) + 100)
            sd.sleep(sleep_duration_ms)


    except sd.PortAudioError as pae:
         print(f"\n[Audio] PortAudio Error: {pae}", file=sys.stderr)
         print("[Audio] This often means an issue with audio device configuration or", file=sys.stderr)
         print("[Audio] selection, or sample rate/format mismatch with device capabilities.", file=sys.stderr)
         stop_event.set()
    except Exception as e:
        print(f"\n[Audio] Error during audio playback setup or stream: {e}", file=sys.stderr)
        stop_event.set()
    finally:
        if stream:
            try:
                if stream.active:
                    print("[Audio] Stopping audio stream...")
                    stream.stop()
                stream.close()
                print("[Audio] Audio stream closed.")
            except AttributeError:
                 print("[Audio] Stream object might not have been fully initialized or already closed.", file=sys.stderr)
            except Exception as e:
                 print(f"[Audio] Error closing audio stream: {e}", file=sys.stderr)
        print("[Audio] Playback thread finished.")

def run_client(voice: str, model: str, output_file: str | None):
    """Runs the gRPC client, optionally saving audio to a file."""
    audio_queue = queue.Queue(maxsize=100)
    stop_playback_event = threading.Event()
    output_stream = None

    player_thread = threading.Thread(
        target=play_audio_thread,
        args=(audio_queue, stop_playback_event),
        daemon=True
    )
    player_thread.start()

    channel = None
    try:
        if output_file:
            try:
                print(f"[File] Opening '{output_file}' for writing raw audio...")
                output_stream = open(output_file, "wb")
                print(f"[File] Saving audio stream to '{output_file}'")
            except IOError as e:
                print(f"\n[Error] Failed to open output file '{output_file}': {e}", file=sys.stderr)
                stop_playback_event.set() 
                return

        print(f"[gRPC] Creating secure channel to {_TARGET_ADDRESS}...")
        credentials = grpc.ssl_channel_credentials()
        channel = grpc.secure_channel(_TARGET_ADDRESS, credentials)

        try:
            grpc.channel_ready_future(channel).result(timeout=10) # Wait 10 seconds
            print("[gRPC] Channel is ready.")
        except grpc.FutureTimeoutError:
            print("[Error] Timeout waiting for gRPC channel to be ready.", file=sys.stderr)
            stop_playback_event.set()
            if output_stream:
                output_stream.close()
            return

        stub = elevenlabs_pb2_grpc.TextToSpeechStub(channel)

        print("[gRPC] Calling StreamingSynthesize RPC...")
        request_gen = generate_requests(voice, model)
        response_iterator = stub.StreamingSynthesize(request_gen)

        print("[gRPC] Receiving audio stream from server...")
        received_chunks = 0
        total_bytes = 0
        for response in response_iterator:
            if stop_playback_event.is_set():
                 print("[gRPC] Playback or file saving stopped unexpectedly. Stopping RPC receive.")
                 break

            if response.audio_chunk:
                received_chunks += 1
                chunk_len = len(response.audio_chunk)
                total_bytes += chunk_len

                if output_stream:
                    try:
                        output_stream.write(response.audio_chunk)
                    except IOError as e:
                        print(f"\n[Error] Failed to write to output file: {e}", file=sys.stderr)
                        stop_playback_event.set()
                        break
                else:
                    try:
                        audio_queue.put(response.audio_chunk, timeout=1.0)
                    except queue.Full:
                        print("[Warning] Audio queue is full. Playback might be lagging or skipping.", file=sys.stderr)
                        time.sleep(0.1)


    except grpc.RpcError as e:
        # Handle different gRPC errors
        print(f"\n[gRPC] Caught RPC Error!", file=sys.stderr)
        status_code = e.code()
        print(f"  Status Code: {status_code}", file=sys.stderr)
        print(f"  Details: {e.details()}", file=sys.stderr)
        if status_code == grpc.StatusCode.UNAUTHENTICATED:
            print("  Hint: Check your API key or authentication method.", file=sys.stderr)
        elif status_code == grpc.StatusCode.INVALID_ARGUMENT:
             print("  Hint: Check voice_id, model_id, or other parameters.", file=sys.stderr)
        elif status_code == grpc.StatusCode.UNAVAILABLE:
             print(f"  Hint: Server at {_TARGET_ADDRESS} might be unreachable.", file=sys.stderr)
        stop_playback_event.set() # Signal threads to stop

    except Exception as e:
        # Catch-all for other unexpected errors
        print(f"\n[Error] An unexpected error occurred in main client logic: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc() # Print stack trace for debugging
        stop_playback_event.set() # Signal threads to stop

    finally:
        print("\n[Main] Shutting down...")

        # Signal playback thread to stop processing queue and exit
        if not stop_playback_event.is_set():
             stop_playback_event.set()

        try:
            audio_queue.put(None, timeout=0.5)
        except queue.Full:
             print("[Main] Audio queue full during shutdown, player thread might take time to exit.", file=sys.stderr)
        except Exception as e:
             print(f"[Main] Error putting sentinel value into queue: {e}", file=sys.stderr)

        if output_stream:
            try:
                output_stream.close()
                print(f"[File] Closed output file '{output_file}'.")
            except IOError as e:
                print(f"[Error] Error closing output file: {e}", file=sys.stderr)


        print("[Main] Waiting for playback thread to complete...")
        player_thread.join(timeout=5.0)
        if player_thread.is_alive():
            print("[Warning] Playback thread did not exit cleanly after 5 seconds.", file=sys.stderr)

        if channel:
            channel.close()
            print("[gRPC] Channel closed.")

        print("[Main] Client finished.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="gRPC Text-to-Speech Client for ElevenLabs")

    parser.add_argument(
        "--voice",
        type=str,
        default="Rachel",
        help="Voice ID to use for synthesis (default: Rachel)"
    )
    parser.add_argument(
        "--model",
        type=str,
        default="eleven_multilingual_v2",
        help="Model ID to use for synthesis (default: eleven_multilingual_v2)"
    )
    parser.add_argument(
        "-o", "--output-file",
        type=str,
        default=None,
        help="Path to save the raw audio stream (PCM S16LE, 24000 Hz, Mono). If not specified, audio is only played back."
    )

    args = parser.parse_args()

    print("--- gRPC Text-to-Speech Client ---")
    print(f"Selected Voice ID:  {args.voice}")
    print(f"Selected Model ID:  {args.model}")
    if args.output_file:
        print(f"Output File:      {args.output_file}")
    else:
        print("Output File:      None (Playback only)")


    try:
        run_client(voice=args.voice, model=args.model, output_file=args.output_file)
    except KeyboardInterrupt:
        print("\n[Main] Keyboard interrupt detected. Exiting.")