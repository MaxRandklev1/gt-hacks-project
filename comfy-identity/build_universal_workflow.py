"""Create the reusable local photo-profile identity-editing workflow."""
import json
from pathlib import Path
import uuid

root=Path(__file__).resolve().parent
workflow=json.loads((root/'Qwen21_Jon_Identity.json').read_text(encoding='utf-8-sig'))
api=json.loads((root/'Qwen21_Jon_Identity.api.json').read_text(encoding='utf-8-sig'))
profile_id=json.loads((root/'universal-default-profile.json').read_text(encoding='utf-8'))['profile_id']
generic=(root/'universal-prompt.txt').read_text(encoding='utf-8').strip()
nodes={n['id']:n for n in workflow['nodes']}
workflow['nodes']=[n for n in workflow['nodes'] if n['id']!=2]
del api['2']

old=nodes[14]
model_link=next(link for link in workflow['links'] if link[1]==4 and link[3]==14)
reference_link=next(link for link in workflow['links'] if link[1]==2 and link[3]==8)
reference_link[1:3]=[14,1]
profile={
    'id':14,'type':'UniversalIdentityProfile','title':'2 - PERSON: upload photos, choose reference, train once',
    'pos':[470,40],'size':[490,850],'flags':{},'order':4,'mode':0,
    'inputs':[{'name':'model','type':'MODEL','link':model_link[0]},
              {'name':'profile_id','type':'COMBO','link':None,'widget':{'name':'profile_id'}},
              {'name':'reference_index','type':'INT','link':None,'widget':{'name':'reference_index'}},
              {'name':'identity_strength','type':'FLOAT','link':None,'widget':{'name':'identity_strength'}},
              {'name':'use_trained_identity','type':'BOOLEAN','link':None,'widget':{'name':'use_trained_identity'}}],
    'outputs':[{'name':'model','type':'MODEL','links':old['outputs'][0]['links']},
               {'name':'reference_image','type':'IMAGE','links':[reference_link[0]]},
               {'name':'trigger','type':'STRING','links':[]},
               {'name':'identity_instruction','type':'STRING','links':[]}],
    'properties':{'Node name for S&R':'UniversalIdentityProfile'},
    'widgets_values':[profile_id,0,0.8,True],
    'widgets_values_named':{'profile_id':profile_id,'reference_index':0,'identity_strength':0.8,'use_trained_identity':True},
}
workflow['nodes']=[profile if n['id']==14 else n for n in workflow['nodes']]
nodes[14]=profile
api['14']={'class_type':'UniversalIdentityProfile','inputs':{'model':['4',0],**profile['widgets_values_named']},'_meta':{'title':profile['title']}}
api['8']['inputs']['images.image_2']=['14',1]
prompt_node={
    'id':15,'type':'UniversalIdentityPrompt','title':'3 - Reusable edit instructions & expression',
    'pos':[1020,40],'size':[620,600],'flags':{},'order':5,'mode':0,
    'inputs':[{'name':'identity_instruction','type':'STRING','link':None},
              {'name':'edit_instructions','type':'STRING','link':None,'widget':{'name':'edit_instructions'}},
              {'name':'expression_mode','type':'COMBO','link':None,'widget':{'name':'expression_mode'}},
              {'name':'expression_details','type':'STRING','link':None,'widget':{'name':'expression_details'}}],
    'outputs':[{'name':'prompt','type':'STRING','links':[]}],
    'properties':{'Node name for S&R':'UniversalIdentityPrompt'},
    'widgets_values':[generic,'Neutral (closed mouth)',''],
    'widgets_values_named':{'edit_instructions':generic,'expression_mode':'Neutral (closed mouth)','expression_details':''},
}
workflow['nodes'].append(prompt_node)
nodes[15]=prompt_node
api['15']={'class_type':'UniversalIdentityPrompt','inputs':dict(prompt_node['widgets_values_named']),'_meta':{'title':prompt_node['title']}}
link_id=max(link[0] for link in workflow['links'])
for source,out,target,name in [(14,3,15,'identity_instruction'),(15,0,8,'prompt')]:
    link_id+=1
    target_slot=next(i for i,s in enumerate(nodes[target]['inputs']) if s['name']==name)
    workflow['links'].append([link_id,source,out,target,target_slot,'STRING'])
    nodes[source]['outputs'][out]['links'].append(link_id)
    nodes[target]['inputs'][target_slot]['link']=link_id
    api[str(target)]['inputs'][name]=[str(source),out]

nodes[1]['title']='1 - BASE: body, clothing, pose and scene'
nodes[1]['pos']=[30,40]
nodes[1]['size']=[380,420]
for ident,pos in {3:[30,560],4:[30,710],6:[30,910],7:[30,1100],5:[1700,440],8:[1020,700],9:[1700,40],10:[1700,650],11:[2110,40],12:[2110,710],13:[470,1160]}.items():
    nodes[ident]['pos']=pos
nodes[4]['widgets_values'][1]=0.65
nodes[4]['widgets_values_named']['strength_model']=0.65
api['4']['inputs']['strength_model']=0.65
nodes[4]['title']='Head-swap strength (texture / transfer balance)'
nodes[8]['widgets_values'][0]=''
nodes[8]['widgets_values_named']['prompt']=''
nodes[8]['widgets_values'][2]=0
nodes[8]['widgets_values_named']['resolution']=0
api['8']['inputs']['resolution']=0
nodes[8]['size']=[620,400]
nodes[8]['title']='Encode inputs - resolution 0 keeps base size'
nodes[9]['widgets_values'][2]=80
nodes[9]['widgets_values_named']['steps']=80
api['9']['inputs']['steps']=80
nodes[9]['title']='4 - Render quality: 80 steps'
prefix='Universal_Identity/result'
api['11']['inputs']['filename_prefix']=prefix
nodes[11]['widgets_values'][0]=prefix
nodes[11]['widgets_values_named']['filename_prefix']=prefix
note='''# Reusable identity workflow
1. Upload the base image on the left.
2. In PERSON, upload a photo folder or choose a saved profile. Click a thumbnail to use it as the reference; check which photos train the identity.
3. Train identity once for a new person, then Run to render. A profile without a trained adapter can run using its reference photo.
4. Choose an expression mode. Neutral is selected for this base; Match base image or Custom work with other poses and expressions.

The selected profile supplies its own identity token and trained adapter. The shared edit instructions contain no fixed person or facial features.
Head-swap strength and identity strength are separate controls. Stronger settings can soften skin; more steps alone may not fix that.
All photo storage and training stay on this PC.'''
nodes[13]['widgets_values']=[note]
nodes[13]['widgets_values_named']={'text':note}
nodes[13]['size']=[1170,250]
workflow.update(id=str(uuid.uuid4()),revision=0,last_node_id=15,last_link_id=link_id,extra={'ds':{'scale':0.44,'offset':[80,30]}})
for key,value in api.items():
    if int(key) in nodes:
        value['_meta']={'title':nodes[int(key)]['title']}
for name,value in [('Qwen21_Universal_Identity.json',workflow),('Qwen21_Universal_Identity.api.json',api)]:
    (root/name).write_text(json.dumps(value,indent=2),encoding='utf-8')
print(json.dumps({'profile_id':profile_id,'nodes':len(workflow['nodes']),'links':len(workflow['links'])}))
