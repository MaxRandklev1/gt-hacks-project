"""Build a minimal three-reference preview from the proven identity graph."""
import json
from pathlib import Path

root=Path(__file__).resolve().parent
p=json.loads((root/'Qwen21_Universal_Identity_TwoPass.api.json').read_text())
p={k:v for k,v in p.items() if int(k) in {1,3,4,5,6,7,8,9,14,15,16,17,18}}
p['1']['inputs']['image']='tryon/pose_base.png'
p['31']={'class_type':'ImageScaleToTotalPixels','inputs':{'image':['1',0],'upscale_method':'lanczos','megapixels':1.0,'resolution_steps':32}}
p['32']={'class_type':'LoadImage','inputs':{'image':'tryon/shirt_reference.png'}}
p['8']['inputs']['images.image_1']=['31',0]
p['8']['inputs']['images.image_3']=['32',0]
p['15']['inputs']['expression_mode']='Match base image'
p['15']['inputs']['edit_instructions']='''head_swap: Use <image1> as the fixed pose and scene. Replace the wearer\'s identity with the person in <image2>, preserving the base head position, scale, rotation, gaze and expression. Take recognizable facial anatomy, hair and appearance from <image2>.
Replace only the upper-body garment with the garment shown in <image3>. Transfer its exact design, colors, print composition, neckline, sleeves and fabric appearance onto the existing torso, adapting the cloth naturally to the pose and lighting. Preserve the complete printed artwork and its relative placement; bend it only to follow the cloth. Faces and people printed on the garment are artwork, never identity references for the wearer. Ignore any background or display form in the garment reference.
Preserve <image1>\'s body proportions, pose, hands, lower-body clothing, accessories, framing, camera, lighting and background. Match exposed skin to the identity reference while retaining local shadows, highlights and natural variation. Keep natural skin texture and fine detail without smoothing, artificial sharpening or a waxy finish. Keep a natural transition between the head and neck.'''
p['18']['inputs']['filename_prefix']='Universal_TryOn/tests/combined_three_reference'
(root/'tryon-results/preview.api.json').write_text(json.dumps(p,indent=2))
print('Preview API ready.')
