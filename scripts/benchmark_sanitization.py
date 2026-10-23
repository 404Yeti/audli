"""AUD-82: CPU and wall time of tutor-audio sanitization strategies on synthetic MP3 (no provider calls).

est_wall_at_0.15cpu_p50 is CPU time / 0.15, an estimate of Render free-tier throttling, not a measurement.
"""
import subprocess,resource,time,statistics as st,tempfile,os,sys,json
from imageio_ffmpeg import get_ffmpeg_exe
F=get_ffmpeg_exe()
d=tempfile.mkdtemp()
def mk(sec):
    p=f'{d}/c{sec}.mp3'
    subprocess.run([F,'-v','error','-y','-f','lavfi','-i',f'sine=f=220:d={sec}','-f','lavfi','-i',f'anoisesrc=d={sec}:a=0.05','-filter_complex','amix=inputs=2','-ar','44100','-b:a','128k','-id3v2_version','3','-metadata','comment=hidden script text',p],check=True)
    return p
modes={
 'reencode(current)':lambda s,o:['-vn','-map_metadata','-1','-t','150','-codec:a','libmp3lame','-id3v2_version','0','-write_id3v1','0',o],
 'copy+strip':lambda s,o:['-vn','-map_metadata','-1','-t','150','-c:a','copy','-id3v2_version','0','-write_id3v1','0','-write_xing','0',o],
 'decode-only':lambda s,o:['-vn','-t','150','-f','null','-'],
}
def run(args):
    r0=resource.getrusage(resource.RUSAGE_CHILDREN);t=time.perf_counter()
    subprocess.run([F,'-v','error','-y','-nostdin','-i',args[0]]+args[1],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,check=True)
    w=time.perf_counter()-t;r1=resource.getrusage(resource.RUSAGE_CHILDREN)
    return w*1000,((r1.ru_utime-r0.ru_utime)+(r1.ru_stime-r0.ru_stime))*1000
out={}
for sec in (3,6,12):
    src=mk(sec);res={}
    for name,f in modes.items():
        o=f'{d}/o_{sec}.mp3'
        if os.path.exists(o): os.remove(o)
        rows=[run((src,f(src,o))) for _ in range(30)]
        w=sorted(r[0] for r in rows);c=sorted(r[1] for r in rows)
        res[name]={'wall_p50':round(w[14]),'wall_p95':round(w[28]),'cpu_p50':round(c[14]),'est_wall_at_0.15cpu_p50':round(c[14]/0.15)}
    out[f'{sec}s']=res
print(json.dumps(out,indent=1))
# hidden metadata survival check
src=mk(3)
for name in('reencode(current)','copy+strip'):
    o=f'{d}/chk.mp3';subprocess.run([F,'-v','error','-y','-i',src]+modes[name](src,o),check=True)
    print(name,'hidden text survives:',b'hidden script' in open(o,'rb').read())
