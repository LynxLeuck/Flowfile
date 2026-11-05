"""Isolated, uninstalled ARM .deb startup probe; not an interactive UI test."""
from pathlib import Path
import os, subprocess, hashlib, json, socket, time, signal, urllib.request, argparse
parser=argparse.ArgumentParser()
parser.add_argument('--package',required=True)
parser.add_argument('--results',required=True)
args=parser.parse_args()
E=Path(args.results).resolve();E.mkdir(parents=True,exist_ok=True)
package=Path(args.package).resolve();assert package.is_file()
root=E/'extracted';assert not root.exists(), 'Preserve prior probe evidence'
subprocess.run(['dpkg-deb','-x',str(package),str(root)],check=True)
for n in ['home','config','cache','data','runtime']:(E/n).mkdir(exist_ok=True)
(E/'runtime').chmod(0o700)
env={'PATH':os.environ['PATH'],'HOME':str(E/'home'),'XDG_CONFIG_HOME':str(E/'config'),'XDG_CACHE_HOME':str(E/'cache'),'XDG_DATA_HOME':str(E/'data'),'XDG_RUNTIME_DIR':str(E/'runtime'),'TMPDIR':os.environ.get('RUNNER_TEMP') or os.environ['TMPDIR'],'POLARS_MAX_THREADS':'2','OMP_NUM_THREADS':'1','OPENBLAS_NUM_THREADS':'1','FLOWFILE_TELEMETRY':'0','FLOWFILE_SCHEDULER_ENABLED':'0','FLOWFILE_KERNEL_GC':'0','FEATURE_FLAG_AI':'0','RUST_LOG':'info'}
def available(port):
 with socket.socket() as s:
  try:s.bind(('127.0.0.1',port));return True
  except OSError:return False
ports=next((p,p+1) for p in range(63578,63778,2) if available(p) and available(p+1))
result={'status':'RUNNING','stage':'extract-and-launch','commit':os.environ.get('GITHUB_SHA'),'package':str(package),'package_sha256':hashlib.sha256(package.read_bytes()).hexdigest(),'expected_ports':ports,'scope':'Shell startup, bundled sidecar readiness and OpenAPI descriptors; no interactive UI assertions.'}
def save():(E/'result.json').write_text(json.dumps(result,indent=2))
def get(port,path,timeout=10):
 with urllib.request.urlopen(f'http://127.0.0.1:{port}{path}',timeout=timeout) as r:return r.status,r.read()
def children():
 rows=subprocess.check_output(['ps','-eo','pid,args'],text=True).splitlines()
 return [int(s.split(None,1)[0]) for s in rows if str(root) in s and int(s.split(None,1)[0])!=os.getpid()]
save();p=None
try:
 with (E/'startup.log').open('w') as log:
  p=subprocess.Popen(['xvfb-run','-a','dbus-run-session','--',str(root/'usr/bin/flowfile')],env=env,cwd=E,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
  deadline=time.monotonic()+150
  while time.monotonic()<deadline:
   assert p.poll() is None, f'Packaged shell exited: {p.returncode}'
   try:
    assert all(get(port,'/docs')[0]==200 for port in ports)
    break
   except Exception:time.sleep(1)
  else:raise RuntimeError('Bundled core/worker not both ready within 150s')
  result.update(stage='openapi',core_docs=200,worker_docs=200);save()
  schema=json.loads(get(ports[0],'/openapi.json',timeout=60)[1]);templates={k:v for k,v in schema['components']['schemas'].items() if k.startswith('NodeTemplate')}
  assert any('inputs' in v.get('properties',{}) and 'outputs' in v.get('properties',{}) for v in templates.values()), 'Packaged API missing descriptor fields'
  result.update(stage='sustained-liveness');save()
  time.sleep(10);assert p.poll() is None
  assert all(get(port,'/docs')[0]==200 for port in ports)
  result.update(status='PASS',core_docs=200,worker_docs=200,descriptor_schemas=list(templates),owned_process_count=len(children()))
except Exception as e:
 result.update(status='FAIL',error=type(e).__name__+': '+str(e))
finally:
 if p is not None and p.poll() is None:
  os.killpg(p.pid,signal.SIGTERM)
  try:p.wait(timeout=15)
  except subprocess.TimeoutExpired:os.killpg(p.pid,signal.SIGKILL);p.wait()
 for _ in range(15):
  if not children():break
  time.sleep(1)
 residual=children()
 for pid in residual:
  try:os.kill(pid,signal.SIGTERM)
  except ProcessLookupError:pass
 time.sleep(2);result['residual_owned_processes']=children();save()
print(json.dumps(result,indent=2))
assert result['status']=='PASS',result.get('error')
assert not result['residual_owned_processes']
