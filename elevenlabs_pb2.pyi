from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from typing import ClassVar as _ClassVar, Mapping as _Mapping, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class StreamingSynthesizeRequest(_message.Message):
    __slots__ = ("config", "input")
    CONFIG_FIELD_NUMBER: _ClassVar[int]
    INPUT_FIELD_NUMBER: _ClassVar[int]
    config: Config
    input: Input
    def __init__(self, config: _Optional[_Union[Config, _Mapping]] = ..., input: _Optional[_Union[Input, _Mapping]] = ...) -> None: ...

class Config(_message.Message):
    __slots__ = ("voice_id", "model_id", "output_format")
    VOICE_ID_FIELD_NUMBER: _ClassVar[int]
    MODEL_ID_FIELD_NUMBER: _ClassVar[int]
    OUTPUT_FORMAT_FIELD_NUMBER: _ClassVar[int]
    voice_id: str
    model_id: str
    output_format: str
    def __init__(self, voice_id: _Optional[str] = ..., model_id: _Optional[str] = ..., output_format: _Optional[str] = ...) -> None: ...

class Input(_message.Message):
    __slots__ = ("text",)
    TEXT_FIELD_NUMBER: _ClassVar[int]
    text: str
    def __init__(self, text: _Optional[str] = ...) -> None: ...

class StreamingSynthesizeResponse(_message.Message):
    __slots__ = ("audio_chunk",)
    AUDIO_CHUNK_FIELD_NUMBER: _ClassVar[int]
    audio_chunk: bytes
    def __init__(self, audio_chunk: _Optional[bytes] = ...) -> None: ...
