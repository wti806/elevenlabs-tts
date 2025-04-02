import grpc
import sys
import queue
import threading
import time
import sounddevice as sd
import elevenlabs_pb2
import elevenlabs_pb2_grpc


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
    config = elevenlabs_pb2.Config(voice_id=voice_id, model_id=model_id, output_format="pcm_24000")
    yield elevenlabs_pb2.StreamingSynthesizeRequest(config=config)

    print("Enter text to synthesize line by line.")
    print("Press Enter on an empty line when finished.")

    while True:
        text = input("> ")
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
            time.sleep(stream.latency + 0.1)

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
            except Exception as e:
                 print(f"[Audio] Error closing audio stream: {e}", file=sys.stderr)
        print("[Audio] Playback thread finished.")


def run_client(voice: str, model: str):
    audio_queue = queue.Queue(maxsize=100)
    stop_playback_event = threading.Event()

    player_thread = threading.Thread(
        target=play_audio_thread,
        args=(audio_queue, stop_playback_event),
        daemon=True
    )
    player_thread.start()

    channel = None
    try:
        credentials = grpc.ssl_channel_credentials()

        # Create a secure channel
        channel = grpc.secure_channel(_TARGET_ADDRESS, credentials)

        # Optionally, you can wait for the channel to be ready (good practice)
        try:
            grpc.channel_ready_future(channel).result(timeout=10)
            print("gRPC channel is ready.")
        except grpc.FutureTimeoutError:
            print("Timeout waiting for gRPC channel to be ready.")
            return

        stub = elevenlabs_pb2_grpc.TextToSpeechStub(channel)

        print("[gRPC] Calling StreamingSynthesize RPC...")
        request_gen = generate_requests(voice, model)
        response_iterator = stub.StreamingSynthesize(request_gen)

        print("[gRPC] Receiving audio stream from server...")
        received_chunks = 0
        for response in response_iterator:
            if stop_playback_event.is_set():
                 print("[gRPC] Playback thread stopped unexpectedly. Stopping RPC receive.")
                 break
            if response.audio_chunk:
                received_chunks += 1
                print("[gRPC] received audio chunk {received_chunks}")
                try:
                     audio_queue.put(response.audio_chunk, timeout=1)
                except queue.Full:
                     print("[Warning] Audio queue is full. Playback might be lagging or skipping.", file=sys.stderr)
                     time.sleep(0.1)

        if not stop_playback_event.is_set():
             print(f"\n[gRPC] Response stream finished. Total audio chunks received: {received_chunks}")

    except grpc.RpcError as e:
        print(f"\n[gRPC] Caught RPC Error!", file=sys.stderr)
        print(f"  Status Code: {e.code()}", file=sys.stderr)
        print(f"  Details: {e.details()}", file=sys.stderr)
        stop_playback_event.set()
    except Exception as e:
        print(f"\n[Error] An unexpected error occurred in main client logic: {e}", file=sys.stderr)
        stop_playback_event.set()
    finally:
        print("[Main] Shutting down...")
      
        if not stop_playback_event.is_set():
             stop_playback_event.set()

        try:
            audio_queue.put_nowait(None)
        except queue.Full:
             print("[Main] Audio queue full during shutdown, player thread might take time to exit.")
        except Exception as e:
             print(f"[Main] Error putting sentinel value: {e}")


        print("[Main] Waiting for playback thread to complete...")
        player_thread.join(timeout=5.0)
        if player_thread.is_alive():
            print("[Warning] Playback thread did not exit cleanly after 5 seconds.", file=sys.stderr)

        if channel:
            channel.close()
            print("[gRPC] Channel closed.")

        print("[Main] Client finished.")


if __name__ == "__main__":
    default_voice = "Rachel"
    default_model = "eleven_multilingual_v2"

    print("--- gRPC Text-to-Speech Client ---")
    print(f"Selected Voice ID:  {default_voice}")
    print(f"Selected Model ID:  {default_model}")

    run_client(voice=default_voice, model=default_model)
