"""Offline Russian synthesis through the pinned sherpa-onnx 1.13.8 C ABI."""
from array import array
import base64
import ctypes as c
import io
import os
import re
import threading
import wave
from pathlib import Path


def fields(names, kind):
    return [(name, kind) for name in names.split()]


class Vits(c.Structure):
    _fields_ = fields('model lexicon tokens data_dir', c.c_char_p) + fields(
        'noise_scale noise_scale_w length_scale', c.c_float) + [('dict_dir', c.c_char_p)]


class Matcha(c.Structure):
    _fields_ = fields('acoustic_model vocoder lexicon tokens data_dir', c.c_char_p) + fields(
        'noise_scale length_scale', c.c_float) + [('dict_dir', c.c_char_p)]


class Kokoro(c.Structure):
    _fields_ = fields('model voices tokens data_dir', c.c_char_p) + [('length_scale', c.c_float)] + fields(
        'dict_dir lexicon lang', c.c_char_p)


class Kitten(c.Structure):
    _fields_ = fields('model voices tokens data_dir', c.c_char_p) + [
        ('length_scale', c.c_float)]


class Zipvoice(c.Structure):
    _fields_ = fields('tokens encoder decoder vocoder data_dir lexicon', c.c_char_p) + fields(
        'feat_scale t_shift target_rms guidance_scale', c.c_float)


class Pocket(c.Structure):
    _fields_ = fields('lm_flow lm_main encoder decoder text_conditioner vocab_json token_scores_json', c.c_char_p) + [
        ('voice_embedding_cache_capacity', c.c_int32)]


class Supertonic(c.Structure):
    _fields_ = fields('duration_predictor text_encoder vector_estimator vocoder tts_json unicode_indexer voice_style', c.c_char_p)


class Model(c.Structure):
    _fields_ = [('vits', Vits), ('num_threads', c.c_int32), ('debug', c.c_int32), ('provider', c.c_char_p),
                ('matcha', Matcha), ('kokoro', Kokoro), ('kitten', Kitten), ('zipvoice', Zipvoice),
                ('pocket', Pocket), ('supertonic', Supertonic)]


class Config(c.Structure):
    _fields_ = [('model', Model), ('rule_fsts', c.c_char_p), ('max_num_sentences', c.c_int32),
                ('rule_fars', c.c_char_p), ('silence_scale', c.c_float)]


class Audio(c.Structure):
    _fields_ = [('samples', c.POINTER(c.c_float)), ('n', c.c_int32), ('sample_rate', c.c_int32)]


class LocalSpeech:
    def __init__(self, runtime_dir: Path):
        self.root = runtime_dir / 'speech'
        self.lock = threading.Lock()
        self.api = None
        self.handle = None

    def _open(self):
        root = self.root.resolve()
        library = root / 'sherpa-onnx-c-api.dll'
        if os.name != 'nt' or not library.is_file():
            raise RuntimeError('Локальный нейроголос недоступен в этой сборке')
        api = c.CDLL(str(library), winmode=0x1100)
        api.SherpaOnnxCreateOfflineTts.argtypes = [c.POINTER(Config)]
        api.SherpaOnnxCreateOfflineTts.restype = c.c_void_p
        api.SherpaOnnxDestroyOfflineTts.argtypes = [c.c_void_p]
        api.SherpaOnnxDestroyOfflineTts.restype = None
        api.SherpaOnnxOfflineTtsGenerate.argtypes = [c.c_void_p, c.c_char_p, c.c_int32, c.c_float]
        api.SherpaOnnxOfflineTtsGenerate.restype = c.POINTER(Audio)
        api.SherpaOnnxDestroyOfflineTtsGeneratedAudio.argtypes = [c.POINTER(Audio)]
        api.SherpaOnnxDestroyOfflineTtsGeneratedAudio.restype = None
        model, tokens, data = (root / name for name in ('ru_RU-dmitri-medium.onnx', 'tokens.txt', 'espeak-ng-data'))
        if not (model.is_file() and tokens.is_file() and data.is_dir()):
            raise RuntimeError('Файлы нейроголоса отсутствуют')
        config = Config()
        config.model.vits = Vits(str(model).encode(), None, str(tokens).encode(), str(data).encode(), .667, .8, 1.0, None)
        config.model.num_threads = 2
        config.model.provider = b'cpu'
        config.max_num_sentences = 1
        config.silence_scale = .2
        handle = api.SherpaOnnxCreateOfflineTts(c.byref(config))
        if not handle:
            raise RuntimeError('Не удалось загрузить нейроголос')
        self.api, self.handle = api, handle

    def prepare(self):
        with self.lock:
            if not self.handle:
                self._open()
            return {'voice': 'dmitri'}

    def render(self, text):
        if not isinstance(text, str) or not 1 <= len(text) <= 400 or '\0' in text:
            raise ValueError('Speech text must contain 1-400 characters')
        text = ' '.join(re.sub(r'\s+', ' ', text).split())
        if not text:
            raise ValueError('Speech text is empty')
        with self.lock:
            if not self.handle:
                self._open()
            audio = self.api.SherpaOnnxOfflineTtsGenerate(self.handle, text.encode(), 0, 1.08)
            if not audio:
                raise RuntimeError('Нейроголос не вернул звук')
            try:
                result = audio.contents
                if not result.samples or not 8000 <= result.sample_rate <= 48000 or not 0 < result.n <= result.sample_rate * 45:
                    raise RuntimeError('Некорректная звуковая дорожка')
                pcm = array('h', (int(max(-1, min(1, result.samples[i])) * 32767) for i in range(result.n)))
                wav = io.BytesIO()
                with wave.open(wav, 'wb') as stream:
                    stream.setparams((1, 2, result.sample_rate, result.n, 'NONE', ''))
                    stream.writeframes(pcm.tobytes())
                return {'wav': base64.b64encode(wav.getvalue()).decode('ascii'), 'voice': 'dmitri'}
            finally:
                self.api.SherpaOnnxDestroyOfflineTtsGeneratedAudio(audio)

    def close(self):
        with self.lock:
            if self.handle:
                self.api.SherpaOnnxDestroyOfflineTts(self.handle)
                self.handle = None
