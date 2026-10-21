import type { RecoveryConnection } from '../services/batchRecovery';

interface Props {
  connection: RecoveryConnection;
  launching: boolean;
  onCheck?: () => void;
  onForget?: () => void;
}

export function RenderRecoveryNotice({connection,launching,onCheck,onForget}: Props) {
  return <div id="studio-render-recovery" role="status" style={{padding:16,marginTop:16,border:'1px solid #a16207',borderRadius:10,background:'#291f10',color:'#fde68a'}}>
    <strong>{connection.mode==='missing'?'Render sebelumnya tidak ditemukan di backend':'Status render sebelumnya belum terkonfirmasi'}</strong>
    <p style={{margin:'8px 0',fontSize:13}}>Aplikasi sedang memeriksa permintaan lama. Ini bukan proses memulai render baru. Cek status atau hasil render; jika backend sudah restart dan job tidak tersedia, lepaskan pemulihan lama untuk membuat batch baru.</p>
    <div style={{display:'flex',gap:10,flexWrap:'wrap'}}>
      {onCheck&&<button type="button" onClick={onCheck}>Cek status render</button>}
      {onForget&&<button type="button" disabled={launching} onClick={onForget}>Lepaskan status render lama</button>}
    </div>
  </div>;
}
