"""Import an already trained local identity and its prepared photos."""
import argparse
import json
from pathlib import Path
import shutil
import sys
import time

root = Path(__file__).resolve().parent
sys.path.insert(0, str(root / 'universal_identity'))
from backend import ProfileStore

parser=argparse.ArgumentParser()
parser.add_argument('--name',required=True)
parser.add_argument('--trigger',required=True)
parser.add_argument('--dataset',type=Path,required=True)
parser.add_argument('--adapter',type=Path,required=True)
parser.add_argument('--steps',type=int,required=True)
args=parser.parse_args()
config={'training_root':str(root/'training'),'profiles_root':str(root/'training/profiles')}
(root/'universal_identity/config.json').write_text(json.dumps(config,indent=2),encoding='utf-8')
store=ProfileStore(config=config)
existing=[p for p in store.list_profiles() if p['name']==args.name and p['trigger']==args.trigger]
if existing:
    print(json.dumps({'profile_id':existing[0]['id'],'already_imported':True}))
    raise SystemExit(0)
rows=[json.loads(line) for line in (args.dataset/'metadata.jsonl').read_text(encoding='utf-8').splitlines() if line.strip()]
profile=store.create(args.name,[(r['file_name'],args.dataset/r['file_name']) for r in rows])
# Import only explicit local files; the node later resolves everything inside its profile.
if isinstance(profile, str):
    profile=store.load(profile)
else:
    profile=store.load(profile['id'])
profile['trigger']=args.trigger
captions={r['file_name']:r['text'] for r in rows}
for photo in profile['photos']:
    photo['caption']=captions[photo['name']]
directory=store.profile_dir(profile['id'])
(directory/'adapters').mkdir(exist_ok=True)
target=directory/'adapters'/('step'+str(args.steps)+'.safetensors')
shutil.copyfile(args.adapter,target)
profile['training']={'status':'completed','job_id':None}
profile['latest_successful']={'job_id':'imported-'+str(args.steps),'steps':args.steps,'completed_at':time.time(),'adapter_relative':target.relative_to(directory).as_posix()}
store.save(profile)
(root/'universal-default-profile.json').write_text(json.dumps({'profile_id':profile['id'],'name':profile['name']},indent=2),encoding='utf-8')
print(json.dumps(store.public(profile),indent=2))
