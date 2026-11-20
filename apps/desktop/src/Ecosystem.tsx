import {useState} from 'react';

export function Ecosystem({lang,onDraft}:{lang:'ru'|'en';onDraft:(text:string)=>void}){
  const ru=lang==='ru';
  const [packet,setPacket]=useState(''),[error,setError]=useState('');
  function importDraft(){
    setError('');
    try{
      if(new TextEncoder().encode(packet).length>65536)throw Error(ru?'Пакет превышает 64 КБ':'Packet exceeds 64 KB');
      const data=JSON.parse(packet);
      if(!data||data.format!=='rainforge.handoff.v1'||data.source!=='raincode'||typeof data.task!=='string'||data.task.length<1||data.task.length>700||typeof data.context!=='string'||data.context.length>3000)throw Error(ru?'Неверный формат пакета RainCode':'Invalid RainCode handoff packet');
      onDraft(data.task+'\n\n'+(ru?'Данные из RainCode для проверки, без разрешений на действия.':'RainCode reference data for review, without action permissions.')+'\n'+data.context);
    }catch(e){setError((e as Error).message)}
  }
  return <section className="agent-panel" aria-label="RainCode and Nexus">
    <h2>RainCode + Nexus</h2>
    <p>{ru?'Проверяйте код в RainCode, переносите находки в Nexus и создавайте исправления в рабочей папке.':'Review code in RainCode, bring findings into Nexus, and create fixes in your workspace.'}</p>
    <p><a href="https://0xvilya.github.io/my-projects/" target="_blank" rel="noreferrer">{ru?'Приложения RainForge':'Explore RainForge projects'} ↗</a></p>
    <label>{ru?'Пакет из RainCode':'RainCode handoff packet'}<textarea aria-label="RainCode handoff packet" maxLength={65536} value={packet} onChange={e=>setPacket(e.target.value)} placeholder='{"format":"rainforge.handoff.v1",...}'/></label>
    <button disabled={!packet.trim()} onClick={importDraft}>{ru?'Проверить и открыть черновик':'Review and open draft'}</button>
    <p className="muted">{ru?'Проверьте текст перед отправкой. Пакет не запускает инструменты и не даёт доступ к браузерному профилю.':'Review the draft before sending. The packet grants no tools or access to a browser profile.'}</p>
    {error&&<p role="alert">{error}</p>}
  </section>;
}
