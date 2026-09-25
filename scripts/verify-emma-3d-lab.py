"""Validate the exported GLB in an actual Chromium renderer; lab only.

Run with the project's browser extra, while the existing preview server runs:
  .venv/Scripts/python scripts/verify-emma-3d-lab.py
"""
import argparse
import json
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
parser=argparse.ArgumentParser()
parser.add_argument("--url",default="https://127.0.0.1:8001/assets/emma-3d-lab/index.html")
args=parser.parse_args()
# Keep in step with ARM_STYLE in scripts/build-emma-3d-lab.py.
ARM_STYLE="fin"           # "fin" | "flipper" | "glove" | None
BONES_BY_STYLE={"glove":56,"flipper":6,"fin":6,None:4}
out=ROOT/"client/assets/emma-3d-lab/build"
out.mkdir(exist_ok=True)

with sync_playwright() as p:
    browser=p.chromium.launch(channel="chrome",headless=True)
    context=browser.new_context(viewport={"width":1280,"height":900},device_scale_factor=1,
                                ignore_https_errors=True)
    page=context.new_page()
    errors=[]
    page.on("pageerror",lambda e:errors.append(str(e)))
    page.goto(args.url+"?manual=1")
    page.wait_for_function("window.emma3d?.ready",timeout=60000)
    # ARM_STYLE below mirrors build-emma-3d-lab.py and must be changed with it:
    # the checks describe the model that is actually built, never a superset. A
    # verify that still asked for gloves would go green against a model that never
    # builds one - this project's oldest test trap, paid for four times.
    # The four-digit glove rig left the model on 2026-09-23 (ARM_STYLE
    # in build-emma-3d-lab.py): the owner replaced it with soft one-piece paddle
    # arms. These checks describe THAT model on purpose - leaving the glove
    # assertions in place would have gone green against a model that never builds
    # a glove, which is this project's oldest test trap. Restore the skin-weight,
    # finger-deformation and arm-length blocks from git history in the same commit
    # that sets ARM_STYLE back to "glove".
    results=page.evaluate("""(expected) => {
      const app=window.emma3d;
      const check=(ok,msg)=>{if(!ok)throw Error(msg)};
      const state=app.getState();
      check(state.boneCount===expected.bones,`Expected ${expected.bones} bones for ARM_STYLE ${expected.style}, got ${state.boneCount}`);
      check(['idle','look'].every(n=>state.clips.includes(n)),'Missing clip');
      check(!state.clips.some(n=>n==='wave'||n==='grip'),'Hand clips are back but these checks were not restored');
      const old=[];app.model.traverse(o=>{if(/Glove|Shoulder|Wrist|Forearm/.test(o.name))old.push(o.name)});
      check(old.length===0,'Glove-rig geometry still in the scene: '+old.join(', '));
      const paddles=[];app.model.traverse(o=>{if(o.isMesh&&o.name.startsWith('Arm'))paddles.push(o.name)});
      const wantArms=expected.style==='flipper'||expected.style==='fin'?2:0;
      check(paddles.length===wantArms,`Arm meshes: expected ${wantArms}, got ${paddles.length}`);
      let nonfinite='';
      app.model.updateMatrixWorld(true);
      app.model.traverse(o=>{
        if(!o.isMesh)return;
        const a=o.geometry.attributes.position;
        for(let i=0;i<a.count;i+=17)if(!Number.isFinite(a.getX(i)+a.getY(i)+a.getZ(i)))nonfinite=o.name;
      });
      check(!nonfinite,'Nonfinite vertex in '+nonfinite);
      // Interrupt an existing blend: the first visible frame must retain its pose.
      app.seek('look',1.7);app.selectClip('idle');
      for(let i=0;i<13;i++)app.step(1/60);
      const before=app.bones.map(b=>b.quaternion.clone());
      app.selectClip('look');app.step(0);
      const jump=Math.max(...app.bones.map((b,i)=>b.quaternion.clone().normalize().angleTo(before[i].normalize())));
      check(jump<1e-5,'Interrupted transition jumps');
      let maxFrameRotation=0,largestFrame='';
      for(let i=0;i<75;i++){
        const prev=app.bones.map(b=>b.quaternion.clone());app.step(1/60);
        app.bones.forEach((b,j)=>{const angle=b.quaternion.clone().normalize().angleTo(prev[j].normalize());if(angle>maxFrameRotation){maxFrameRotation=angle;largestFrame=`${i} ${b.name} ${angle}`;}});
      }
      check(maxFrameRotation<.20,`Large single-frame rotation: ${largestFrame}`);
      // Check every baked sample, including the middle of the turn.
      // Quaternion sign flips in Blender used to hide between our pose samples.
      let maxClipRotation=0,largestClipStep='';
      for(const clip of app.clips)for(const track of clip.tracks){
        if(!track.name.endsWith('.quaternion'))continue;
        const previous=app.bones[0].quaternion.clone(),next=previous.clone();
        for(let i=4;i<track.values.length;i+=4){
          previous.fromArray(track.values,i-4).normalize();
          next.fromArray(track.values,i).normalize();
          const angle=previous.angleTo(next);
          if(angle>maxClipRotation){maxClipRotation=angle;largestClipStep=`${clip.name} ${track.name} ${track.times[i/4]}`;}
        }
      }
      check(maxClipRotation<.35,`Baked animation flips: ${largestClipStep} ${maxClipRotation}`);
      return {state,interruptedTransitionJump:jump,maxFrameRotation,maxClipRotation,largestClipStep};
    }""",{"bones":BONES_BY_STYLE[ARM_STYLE],"style":ARM_STYLE})
    for name,time,view in [("front",0,"front"),("side",0,"side"),("back",0,"back"),
                           ("look",1.2,"front"),("quarter",0,"quarter")]:
        clip="look" if name=="look" else "idle"
        page.evaluate("([clip,time,view])=>{emma3d.seek(clip,time);emma3d.view(view);}",[clip,time,view])
        page.wait_for_timeout(120)
        page.screenshot(path=str(out/("web-"+name+".png")))
    # Exercise actual controls, not only the inspection API.
    page.locator('[data-clip="look"]').click()
    assert page.locator('[data-clip="look"]').get_attribute("aria-pressed")=="true"
    page.locator('#skeleton').check()
    page.locator('[data-view="quarter"]').click()
    page.screenshot(path=str(out/"web-skeleton.png"))
    page.locator('#skeleton').uncheck()
    page.locator('#play').click()
    assert page.locator('#play').get_attribute("aria-pressed")=="false"
    page.locator('#timeline').fill("0.5")
    assert abs(page.evaluate("emma3d.getState().time")-page.evaluate("emma3d.clips.find(c=>c.name==='look').duration")*.5)<.01
    page.set_viewport_size({"width":390,"height":844})
    assert page.evaluate("document.documentElement.scrollWidth<=innerWidth")
    page.screenshot(path=str(out/"web-mobile.png"))
    assert not errors,errors
    results["page_errors"]=errors
    results["ui_checks"]=["clip button","skeleton toggle","view button","pause","seek","mobile width"]
    context.close()
    # A real-time recording to review the head turn and the interrupted return.
    recording=browser.new_context(viewport={"width":1280,"height":900},ignore_https_errors=True,
        record_video_dir=str(out/"recordings"),record_video_size={"width":1280,"height":900})
    video_page=recording.new_page()
    video_page.goto(args.url)
    video_page.wait_for_function("window.emma3d?.ready",timeout=60000)
    video_page.locator('[data-clip="look"]').click()
    video_page.wait_for_timeout(3400)
    video_page.locator('[data-clip="idle"]').click()
    video_page.wait_for_timeout(1500)
    video=video_page.video
    recording.close()
    video.save_as(str(out/"motion-review.webm"))
    browser.close()
    (out/"browser-audit.json").write_text(json.dumps(results,indent=2),encoding="utf-8")
    print(json.dumps(results,indent=2))
