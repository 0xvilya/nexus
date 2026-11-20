"""Read-only resource assessment for the existing llama.cpp runtime.

This is an estimate, not a reservation, memory limit, compatibility test, or
expert-cache implementation. The runtime remains the authority on loading.
"""
from __future__ import annotations

import ctypes
import os
import shutil
from pathlib import Path

from .gguf import metadata


PROFILES = {
    'economy': (1024, 2 * 1024**3),
    'balanced': (2048, 2 * 1024**3),
    'performance': (4096, 2 * 1024**3),
    'experimental': (1024, 3 * 1024**3),
}


def physical_memory():
    """Windows physical RAM only; never include the page file or GPU memory."""
    if os.name != 'nt':
        return None, None

    class MemoryStatus(ctypes.Structure):
        _fields_ = [('length', ctypes.c_ulong), ('load', ctypes.c_ulong),
                    ('total_phys', ctypes.c_ulonglong), ('avail_phys', ctypes.c_ulonglong),
                    ('total_page', ctypes.c_ulonglong), ('avail_page', ctypes.c_ulonglong),
                    ('total_virtual', ctypes.c_ulonglong), ('avail_virtual', ctypes.c_ulonglong),
                    ('avail_extended', ctypes.c_ulonglong)]

    info = MemoryStatus()
    info.length = ctypes.sizeof(info)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.GlobalMemoryStatusEx.argtypes = [ctypes.POINTER(MemoryStatus)]
    kernel.GlobalMemoryStatusEx.restype = ctypes.c_int
    if not kernel.GlobalMemoryStatusEx(ctypes.byref(info)):
        return None, None
    return int(info.total_phys), int(info.avail_phys)



def model_structure(info):
    """Report declarations, not tensor validation or a runtime support claim."""
    prefix = info['general.architecture'] + '.'

    def integer(suffix, default=None, allow_zero=False):
        value = info.get(prefix + suffix, default)
        minimum = 0 if allow_zero else 1
        return value if type(value) is int and minimum <= value <= 2**31 - 1 else None

    heads = integer('attention.head_count')
    embedding = integer('embedding_length')
    head_length = embedding // heads if heads and embedding and embedding % heads == 0 else None
    experts = integer('expert_count', 0, allow_zero=True)
    shared = integer('expert_shared_count', 0, allow_zero=True)
    active = integer('expert_used_count', allow_zero=True)
    if experts is None or shared is None or (active and not experts):
        kind = 'unknown'
    else:
        kind = 'moe' if experts or shared else 'dense'
    return {
        'kind': kind, 'layers': integer('block_count'), 'embedding': embedding,
        'attention_heads': heads, 'kv_heads': integer('attention.head_count_kv', heads),
        'key_head_length': integer('attention.key_length', head_length),
        'value_head_length': integer('attention.value_length', head_length),
        'training_context': integer('context_length'),
        'expert_count': experts, 'active_expert_count': active, 'shared_expert_count': shared,
    }


def kv_estimate(structure, context):
    """Full-context F16 K/V tensor bytes for homogeneous standard attention.

    One sequence, every layer, no sliding-window compression. Backend alignment,
    compute buffers and allocator overhead are excluded. Arrays or inconsistent
    dimensions yield unknown instead of a plausible-looking number.
    """
    dimensions = [structure[key] for key in ('layers', 'kv_heads', 'key_head_length', 'value_head_length')]
    if not all(dimensions) or not structure['attention_heads']:
        return None
    if structure['kv_heads'] > structure['attention_heads']:
        return None
    if structure['attention_heads'] % structure['kv_heads']:
        return None
    layers, heads, key_length, value_length = dimensions
    return layers * heads * (key_length + value_length) * context * 2

def plan(runtime, filename, profile='balanced'):
    """Assess a model already addressable by NativeRuntime; no loading occurs."""
    if not isinstance(profile, str) or profile not in PROFILES:
        raise ValueError('Unknown SmartLoad profile')
    path = runtime.model_path(filename)
    if not path.is_file():
        raise ValueError('Model file is not available')
    info = metadata(path)  # Header metadata alone never establishes compatibility.
    file_size = path.stat().st_size
    structure = model_structure(info)
    total, available = physical_memory()
    try:
        disk_free = shutil.disk_usage(path.parent).free
    except OSError:
        disk_free = None
    context, floor = PROFILES[profile]
    # Reserve a portion of total physical memory in addition to already-used RAM.
    reserve = max(floor, total // 8) if total is not None else None
    budget = max(0, available - reserve) if available is not None and reserve is not None else None
    warnings = [
        'Оценка не является ограничением RAM или проверкой успешного запуска модели.',
        'Доступная VRAM неизвестна: предложен CPU, GPU-слои автоматически не подбираются.',
        'Чтение заголовка GGUF не подтверждает целостность, совместимость или генерацию.',
    ]
    # Lower the suggested context when physical memory is already scarce.
    # This is only a conservative suggestion; it cannot guarantee fit.
    max_context = 1024 if available is None else 512 if available < 4 * 1024**3 else 1024 if available < 8 * 1024**3 else 8192
    if context > max_context:
        context = max_context
        warnings.append('Рекомендуемый контекст уменьшен из-за недостатка или неизвестного объёма свободной RAM; запуск всё равно может потребовать больше памяти.')
    trained_context = structure['training_context']
    if trained_context and context > trained_context:
        if trained_context >= 512:
            context = max(512, trained_context // 512 * 512)
            warnings.append('Рекомендуемый контекст ограничен объявленной длиной обучения модели.')
        else:
            warnings.append('Объявленный контекст модели меньше минимальных 512 токенов текущего runtime; подходящая конфигурация не подтверждена.')
    kv_bytes = kv_estimate(structure, context)
    if kv_bytes is None:
        warnings.append('Размер KV-кэша неизвестен: размеры attention отсутствуют, неоднородны или некорректны.')
    else:
        warnings.append('KV — оценка F16 для одной последовательности и полного контекста. Выравнивание, вычислительные буферы и накладные расходы backend не включены.')
        if budget is not None and file_size + kv_bytes > budget:
            warnings.append('Полное размещение файла весов и F16 KV-кэша превышает бюджет RAM; эта сумма не включает накладные расходы, а mmap может вызывать дисковое чтение.')
        if budget is not None and kv_bytes >= budget:
            warnings.append('Один KV-кэш достигает ориентировочного бюджета RAM; уменьшите контекст или выберите меньшую модель.')
    if structure['kind'] == 'moe':
        warnings.append('Заголовок объявляет MoE-экспертов. Их тензоры, запуск, CPU-offload и SSD-streaming этой модели не проверены.')
        if structure['active_expert_count'] is None or not structure['expert_count'] or not 1 <= structure['active_expert_count'] <= structure['expert_count']:
            warnings.append('Количество активируемых экспертов отсутствует или противоречит общему числу экспертов.')
    if budget is None:
        warnings.append('Не удалось определить доступную физическую RAM.')
    elif file_size > budget:
        warnings.append('Размер файла превышает ориентировочный бюджет RAM. mmap может читать веса по требованию; успех и скорость не гарантированы, возможна нагрузка на диск и подкачку.')
    if profile == 'experimental':
        warnings.append('Экспериментальный экспертный SSD-streaming и prefetch в текущем backend не реализованы; выбран обычный CPU runtime.')
    return {
        'profile': profile,
        'model': {'filename': filename, 'bytes': file_size,
                  'architecture': info.get('general.architecture'),
                  'quantization_id': info.get('general.file_type'),
                  **structure},
        'hardware': {'ram_total_bytes': total, 'ram_available_bytes': available,
                     'disk_free_bytes': disk_free, 'cpu_threads': os.cpu_count(),
                     'vram_available_bytes': None},
        'recommendation': {'context': context, 'mode': 'cpu', 'gpu_layers': 12},
        'estimate': {'ram_reserve_bytes': reserve, 'ram_budget_bytes': budget,
                     'model_bytes': file_size, 'kv_cache_bytes': kv_bytes,
                     'kv_cache_type': 'f16', 'kv_cache_ram_bytes': kv_bytes,
                     'kv_cache_vram_bytes': 0,
                     'weights_plus_kv_bytes': file_size + kv_bytes if kv_bytes is not None else None,
                     'kv_placement_assumption': 'cpu_recommendation',
                     'estimate_only': True},
        'warnings': warnings,
        'limits_enforced': False,
    }
