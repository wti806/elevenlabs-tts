import logging
import os
from concurrent import futures
from dotenv import load_dotenv

import grpc

import elevenlabs_pb2
import elevenlabs_pb2_grpc

from elevenlabs.client import ElevenLabs


logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
load_dotenv()
_PORT = os.environ.get('PORT', '50051')
_SERVER_ADDRESS = f'[::]:{_PORT}'



class TextToSpeechServicer(elevenlabs_pb2_grpc.TextToSpeechServicer):
    def __init__(self, eleven_client: ElevenLabs):
        self.eleven_client = eleven_client

    def StreamingSynthesize(self, request_iterator, context):
        logging.info(f"Client connected: {context.peer()}")
        config: elevenlabs_pb2.Config = None

        try:
            first_request = next(request_iterator)
            if first_request.WhichOneof('request') != 'config':
                logging.error("First request from client was not a Config message.")
                context.abort(grpc.StatusCode.FAILED_PRECONDITION, "The first message sent must be a Config message.")
                return

            config = first_request.config
            logging.info(f"Received Config: voice_id='{config.voice_id}', model_id='{config.model_id}'")

            def text_chunk_generator():
                logging.info("Starting text input generator.")
                try:
                    for request in request_iterator:
                        if request.WhichOneof('request') != 'input':
                            continue
                        text = request.input.text
                        logging.info(f"Yielding text chunk: '{text}'")
                        yield text

                    logging.info("gRPC input stream finished. Text generator completing.")
                except Exception as e:
                     logging.error(f"Text Gen: Error during iteration: {e}", exc_info=True)

            logging.info("Calling ElevenLabs client...")
            try:
                audio_stream = self.eleven_client.generate(
                    text=text_chunk_generator(),
                    voice=config.voice_id,
                    model=config.model_id,
                    stream=True,
                    output_format=config.output_format,
                )

                logging.info("Streaming audio from ElevenLabs client back to client...")
                for audio_chunk in audio_stream:
                    if not context.is_active():
                         logging.warning("gRPC client disconnected during audio streaming.")
                         break

                    if audio_chunk:
                        logging.info(f"Sending audio chunk (size: {len(audio_chunk)}) to client.")
                        yield elevenlabs_pb2.StreamingSynthesizeResponse(audio_chunk=audio_chunk)

                logging.info(f"Finished streaming audio back to client.")

            except Exception as e:
                logging.error(f"Error during ElevenLabs generate/stream call: {e}", exc_info=True)
                context.abort(grpc.StatusCode.INTERNAL, f"Failed to process TTS stream: {e}")

        except Exception as e:
            logging.exception(f"Unexpected error during StreamingSynthesize: {e}")
            if context.is_active():
                context.abort(grpc.StatusCode.INTERNAL, "An unexpected internal server error occurred.")
               
        finally:
             logging.info(f"StreamingSynthesize call finished.")


def serve():
    api_key = os.getenv("ELEVENLABS_API_KEY")
    if not api_key:
        logging.critical("FATAL: ELEVENLABS_API_KEY environment variable not set at startup.")
        print("Error: ELEVENLABS_API_KEY environment variable not set.")
        return
    try:
        eleven_client = ElevenLabs(api_key=api_key)
        logging.info("Synchronous ElevenLabs client initialized successfully.")
    except Exception as e:
         logging.critical(f"FATAL: Failed to initialize ElevenLabs client: {e}", exc_info=True)
         print(f"Error: Failed to initialize ElevenLabs client: {e}")
         return
    
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))

    elevenlabs_pb2_grpc.add_TextToSpeechServicer_to_server(
        TextToSpeechServicer(eleven_client), server
    )

    server.add_insecure_port(_SERVER_ADDRESS)
    logging.info(f"Starting gRPC server on {_SERVER_ADDRESS}")

    server.start()
    logging.info("Server started successfully. Waiting for connections...")

    server.wait_for_termination()
    server.stop(10)

if __name__ == '__main__':
    serve()