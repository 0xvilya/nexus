import json
import re
import shutil
import subprocess
import urllib.request
from pathlib import Path
from uuid import uuid4
from rainforge_native.download import sha256
from rainforge_native.gguf import metadata
from rainforge_recovery import atomic_json


def import_model(models_dir, source):
    path = Path(source)
    if not path.is_absolute() or path.suffix.lower() != '.gguf' or not path.is_file():
        raise ValueError('Укажите полный путь к файлу .gguf')
    info = metadata(path)
    models_dir.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(models_dir).free < path.stat().st_size + 64*1024*1024:
        raise ValueError('Недостаточно места для копии модели')
    digest = sha256(path)
    filename = digest[:16] + '.gguf'
    target = models_dir / filename
    if not target.exists():
        temporary = models_dir / (uuid4().hex + '.part')
        try:
            shutil.copyfile(path, temporary)
            if sha256(temporary) != digest: raise ValueError('Файл изменился во время импорта')
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
    index = models_dir / 'custom.json'
    items = json.loads(index.read_text(encoding='utf-8')) if index.exists() else []
    item = dict(id=digest, name=path.stem, filename=filename, sha256=digest, bytes=target.stat().st_size,
                architecture=info['general.architecture'], parameters='Своя модель', quantization=str(info.get('general.file_type','GGUF')),
                license='Проверьте лицензию источника', license_url='', source='Локальный импорт',
                ram_estimate_bytes=target.stat().st_size+1024**3, capabilities=['chat'])
    atomic_json(index, [m for m in items if m['id'] != digest] + [item])
    return item


def connector_query(provider, query):
    from urllib.parse import quote
    if not isinstance(query,str) or not 1 <= len(query) <= 200:
        raise ValueError('Введите запрос до 200 символов')
    if provider in ('huggingface_datasets','huggingface_spaces'):
        kind = 'datasets' if provider == 'huggingface_datasets' else 'spaces'
        url = 'https://huggingface.co/api/'+kind+'?search='+quote(query,safe='')+'&limit=8'
    elif provider == 'gitlab':
        url = 'https://gitlab.com/api/v4/projects?visibility=public&search='+quote(query,safe='')+'&per_page=8'
    elif provider == 'wikipedia':
        url = 'https://ru.wikipedia.org/w/rest.php/v1/search/page?q='+quote(query,safe='')+'&limit=8'
    elif provider == 'huggingface':
        url = 'https://huggingface.co/api/models?search='+quote(query)+'&limit=8'
    elif provider == 'github':
        url = 'https://api.github.com/search/repositories?q='+quote(query)+'&per_page=8'
    else:
        raise ValueError('Неизвестный коннектор')
    request = urllib.request.Request(url, headers={'User-Agent':'RainForge-NEXUS','Accept':'application/json'})
    with urllib.request.urlopen(request,timeout=15) as response:
        payload = response.read(2*1024*1024+1)
        if len(payload)>2*1024*1024: raise ValueError('Ответ превышает лимит')
        data = json.loads(payload)
    if provider == 'github':
        return [{'name':r['full_name'],'description':r.get('description'),'url':r['html_url']} for r in data['items']]
    if provider == 'gitlab':
        return [{'name':r['path_with_namespace'],'description':r.get('description'),'url':r['web_url']} for r in data]
    if provider == 'wikipedia':
        return [{'name':r['title'],'description':re.sub('<[^>]+>', '',r.get('excerpt','')),'url':'https://ru.wikipedia.org/wiki/'+quote(r['key'],safe='')} for r in data['pages']]
    if provider in ('huggingface_datasets','huggingface_spaces'):
        return [{'name':r['id'],'description':kind,'url':'https://huggingface.co/'+kind+'/'+r['id']} for r in data]
    return [{'name':r['id'],'description':r.get('pipeline_tag','GGUF / model'),'url':'https://huggingface.co/'+r['id']} for r in data]


def detect_gpu():
    try:
        result = subprocess.run(['powershell.exe','-NoProfile','-Command',
            'Get-CimInstance Win32_VideoController | Select-Object Name,AdapterRAM | ConvertTo-Json -Compress'],
            capture_output=True,text=True,timeout=10,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        data=json.loads(result.stdout); data=data if isinstance(data,list) else [data]
        return data
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return []
