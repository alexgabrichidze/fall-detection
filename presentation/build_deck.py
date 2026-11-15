"""Build an editable PowerPoint and shared scene data for the Figma deck.

Run in an isolated tools environment with python-pptx installed. This script has
no runtime relationship to the fall detection application.
"""
import json
from pathlib import Path

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE, MSO_CONNECTOR
from pptx.enum.text import MSO_ANCHOR, MSO_AUTO_SIZE
from pptx.util import Inches, Pt

OUT = Path(__file__).resolve().parent
C = dict(dark='142522', surface='213A34', ivory='F5F2E9', ink='182D27', mint='B8F3D2',
         muted='52675F', light='DCE8E1', orange='DD916D', pale='E5EADF', white='FFFFFF')
slides = []


def text(value, x, y, w, size=40, color='ink', font='DM Sans', bold=False, h=None):
    return dict(type='text', text=value, x=x, y=y, w=w,
                h=h or size * 1.23 * (value.count('\n') + 1), size=size,
                color=C.get(color, color), font=font, bold=bold)


def rect(x, y, w, h, color, radius=0):
    return dict(type='rect', x=x, y=y, w=w, h=h, color=C.get(color, color), radius=radius)


def line(x, y, x2, y2, color='mint', thickness=4):
    return dict(type='line', x=x, y=y, x2=x2, y2=y2, color=C.get(color, color), thickness=thickness)


def circle(x, y, diameter, color='mint'):
    return dict(type='ellipse', x=x, y=y, w=diameter, h=diameter, color=C.get(color, color))


def stack(x, y, w, items, gap=20, bg=None, padding=0, h=None):
    height = h or sum(t['h'] for t in items) + gap * (len(items) - 1) + padding * 2
    for t in items:
        t['x'], t['y'], t['w'] = padding, padding, w - padding * 2
    return dict(type='stack', x=x, y=y, w=w, h=height, children=items,
                gap=gap, padding=padding, color=C.get(bg, bg) if bg else None)


def t(value, size=40, color='ink', bold=False, font='DM Sans', h=None):
    return text(value, 0, 0, 0, size, color, font, bold, h)


def bracket(x, y, w, h, color='mint', length=54):
    return [line(x, y, x+length, y, color), line(x, y, x, y+length, color),
            line(x+w-length, y, x+w, y, color), line(x+w, y, x+w, y+length, color),
            line(x, y+h-length, x, y+h, color), line(x, y+h, x+length, y+h, color),
            line(x+w-length, y+h, x+w, y+h, color), line(x+w, y+h-length, x+w, y+h, color)]


def add(name, bg, elements, notes):
    index = len(slides)+1
    isdark = bg == 'dark'
    elements += [text('FALL DETECTION  /  EXPERIMENTAL PROTOTYPE', 90, 1018, 1500, 20,
                      'light' if isdark else 'muted', bold=True),
                 text(f'{index:02d} / 10', 1700, 1018, 160, 20, 'light' if isdark else 'muted')]
    slides.append(dict(name=name, background=C[bg], elements=elements, notes=notes))


# 01 — Editorial cover, native landmark diagram, camera-focus motif.
e = [text('SCIENCE FAIR / HACKATHON', 96, 84, 1200, 26, 'mint', bold=True),
     stack(90, 250, 1050, [t('FALL\nDETECTION', 154, 'ivory', True, 'Space Grotesk'),
                          t('Local vision. A human response.', 46, 'light')], gap=30),
     text('A privacy-first desktop prototype', 98, 824, 1050, 38, 'light'),
     rect(1230, 150, 570, 740, 'surface', 18)]
e += bracket(1270, 190, 490, 660)
# Schematic landmarks, not a real frame or model output.
points = [(1505,290),(1435,390),(1580,390),(1360,505),(1650,485),(1410,610),(1590,610),(1370,745),(1630,745)]
for a,b in [(1,2),(1,3),(2,4),(1,5),(2,6),(5,6),(5,7),(6,8)]:
    e.append(line(*points[a], *points[b], 'mint', 7))
for x,y in points:
    e.append(circle(x-9,y-9,18,'ivory'))
e += [text('POSE LANDMARKS',1330,785,380,24,'mint',bold=True),
      text('Concept schematic',1325,913,450,24,'light')]
add('01 / Fall detection', 'dark', e,
    'Introduce this as an experimental computer-based prototype. The intent is to detect a possible fall locally, ask whether the person is okay, and notify one caregiver after a response window. The application workflow is implemented and tested with simulated inputs. Live model integration is pending. The drawing is a conceptual landmark diagram, not model evidence.')

# 02 — Research question with open space and three linked response requirements.
e = [text('01  /  THE QUESTION',96,82,1400,26,'muted',bold=True),
     text('What happens\nafter a possible fall?',90,190,1660,112,'ink','Space Grotesk',True),
     text('Can a webcam start a useful response workflow\nwhile keeping video on the computer?',98,515,1550,46,'muted'),
     line(100,760,1800,760,'ink',3)]
for x,n,title,body in [(100,'01','Notice','Recognize possible fall evidence.'),(690,'02','Check in','Give the person a way to cancel.'),(1280,'03','Reach out','Notify one caregiver if time expires.')]:
    e += [circle(x,744,32,'ink'),stack(x,810,510,[t(n,24,'muted',True),t(title,46,'ink',True,'Space Grotesk'),t(body,32,'muted',h=90)],gap=12)]
add('02 / Research question','ivory',e,
    'Frame the engineering question rather than citing unverified medical statistics. Detection alone is only one part of the system. The response workflow must handle uncertainty, let a person cancel, and show whether a message was accepted. Hypothesis: local vision can be combined with a deterministic response workflow; detection performance still needs evaluation.')

# 03 — Horizontal intended flow with an explicit cancellation branch.
e = [text('02  /  THE COMPLETE FLOW',96,82,1450,26,'mint',bold=True),
     text('Detect. Ask. Allow a response.',90,185,1730,96,'ivory','Space Grotesk',True),
     text('Intended end-to-end behavior',98,330,1500,36,'light')]
labels=[('01','Possible fall','Local vision evidence'),('02','“Are you okay?”','Play one recorded prompt'),('03','Response window','20-second default countdown'),('04','Caregiver alert','One text-only attempt')]
for i,(n,title,subtitle) in enumerate(labels):
    x=96+i*450
    e += [stack(x,470,390,[t(n,30,'mint',True),t(title,44,'ivory',True,'Space Grotesk',h=110),t(subtitle,32,'light',h=90)],gap=15,bg='surface',padding=28,h=330)]
    if i<3:
        e += [line(x+395,633,x+440,633,'mint',4)]
e += [line(1190,806,1190,880,'mint',4),
      text('CANCELLED',980,895,450,30,'mint',bold=True),
      text('Button or keyboard. No alert for a cancelled incident.',96,950,1680,32,'light')]
add('03 / Intended response flow','dark',e,
    'Walk from left to right. This is the intended complete flow, not a claim that live detection is already working. Twenty seconds is a configurable prototype default, not a validated clinical timing recommendation. Cancellation must occur before expiry. Telegram accepting a message does not mean the caregiver has responded. Voice cancellation is optional future work.')

# 04 — Local privacy boundary and narrow outgoing text channel.
e = [text('03  /  PRIVACY BY DESIGN',96,82,1400,26,'muted',bold=True),
     text('Video stays here.',90,185,1540,108,'ink','Space Grotesk',True),
     rect(96,385,1190,465,'pale',20),
     text('ON THE COMPUTER',140,420,1000,26,'muted',bold=True),
     stack(140,505,1050,[t('Webcam + pose + fall model',48,'ink',True,'Space Grotesk'),
                       t('Safety state, countdown, local controls',38,'ink'),
                       t('Frames discarded after processing',36,'muted')],gap=28),
     line(1290,610,1435,610,'ink',5),
     stack(1460,460,360,[t('TELEGRAM',26,'muted',True),t('One\ncaregiver',56,'ink',True,'Space Grotesk'),t('Alert text only',34,'muted')],gap=20),
     text('No saved video or captured microphone recordings.',98,900,1650,37,'ink')]
e += bracket(116,405,1150,425,'muted',32)
add('04 / Privacy boundary','ivory',e,
    'Describe the target architecture. Frames and pose landmarks are local, transient data. The microphone is not used in the current version. Telegram requires internet access and receives only necessary alert text. Camera capture and local inference are A\'s pending integration work. There is no database or persistent alert history. This is not a claim that Telegram is a local service.')

# 05 — Planned vision pipeline, explicit model verification gate.
e = [text('04  /  VISION ENGINEERING',96,82,1400,26,'muted',bold=True),
     text('Pretrained does not\nmean plug-and-play.',90,180,1680,98,'ink','Space Grotesk',True)]
for i,(title,sub) in enumerate([('OpenCV','Capture timestamped frames'),('MediaPipe Pose','Extract body landmarks'),('Transformer + LiteRT','Evaluate a verified input sequence')]):
    y=475+i*148
    e += [text(f'0{i+1}',100,y,110,42,'muted',bold=True),
          stack(245,y,690,[t(title,44,'ink',True,'Space Grotesk'),t(sub,32,'muted')],gap=8)]
    if i<2:e += [line(150,y+65,150,y+130,'muted',3)]
e += [stack(1060,475,745,[t('LIVE DETECTION: PENDING',26,'ink',True),
                            t('Verify the\nmodel’s inputs.',64,'ink',True,'Space Grotesk'),
                            t('Landmark order · coordinates\nnormalization · timing · sequence length',34,'ink',h=100)],gap=24,bg='orange',padding=40,h=440)]
add('05 / Model verification gate','ivory',e,
    'This entire live vision pipeline remains pending. A is evaluating the punpayut Fall-Detection Transformer checkpoint as a TFLite model with LiteRT. Before live decisions, establish training/runtime preprocessing, tensor shapes, landmark order, coordinates, normalization, cadence, sequence length, and output class meaning. A correctly shaped inference call proves compatibility only, not accuracy. Missing pose remains unknown and must not trigger an emergency by itself.')

# 06 — State progression and invariant-focused application engineering.
e = [text('05  /  SAFETY ENGINEERING',96,82,1500,26,'mint',bold=True),
     text('One incident. One countdown.',90,185,1730,96,'ivory','Space Grotesk',True),
     text('IMPLEMENTED / TESTED WITH SIMULATED EVIDENCE',98,330,1680,28,'mint',bold=True)]
for i,label in enumerate(['Monitoring','Suspected fall','Awaiting response','Alerting']):
    x=96+i*450
    e += [stack(x,485,390,[t(f'0{i+1}',28,'mint',True),t(label,43,'ivory',True,'Space Grotesk',h=118)],gap=24,bg='surface',padding=30,h=260)]
    if i<3:e += [line(x+395,615,x+440,615,'mint',4)]
e += [stack(98,810,785,[t('Repeated detections do not restart time.',36,'ivory'),t('Cancellation or delivery requires explicit reset.',32,'light')],gap=18),
      stack(1040,810,760,[t('Camera loss does not cancel an incident.',36,'ivory'),t('Network requests never block the countdown.',32,'light')],gap=18)]
add('06 / Deterministic safety logic','dark',e,
    'A supplies detection evidence; B owns incidents and alert decisions. Countdown timing uses a monotonic clock. Tests cover cancellation immediately before expiry, exact-deadline behavior, repeated detections, reset barriers, camera/worker failure, and stalled transport. After cancellation or delivery, the operator must explicitly reset. No automatic notification retry occurs. Delivery acceptance is not caregiver acknowledgement.')

# 07 — Sparse, clearly labelled interaction schematic and demo cue sheet.
e = [text('06  /  DEMO',96,82,1400,26,'ink',bold=True),
     text('SIMULATED INPUT · INTERACTION SCHEMATIC',96,145,1400,25,'muted',bold=True),
     stack(100,330,960,[t('Are you okay?',94,'ink',True,'Space Grotesk'),
                       t('20 s',178,'ink',True,'Space Grotesk'),
                       t('Response window',35,'muted')],gap=20),
     stack(100,800,740,[t('I’m okay / Cancel',44,'ivory',True,'Space Grotesk')],bg='dark',padding=30,h=120),
     stack(1210,355,605,[t('01  Press F',46,'ink',True,'Space Grotesk'),t('Inject a simulated possible fall.',34,'muted',h=92),
                         t('02  Press C',46,'ink',True,'Space Grotesk'),t('Cancel before countdown expiry.',34,'muted',h=92),
                         t('03  Reset, then repeat',44,'ink',True,'Space Grotesk'),t('Let time expire; inspect simulated delivery.',34,'muted',h=100)],gap=20),
     line(1115,335,1115,915,'muted',3)]
# The mint demo slide uses the same mint foreground as its background only in no elements.
slides_before=len(slides)
add('07 / Simulated demo','mint',e,
    'Before presenting, run: .venv/bin/fall-detection --detector fake --ui --scenario idle. Press F to start the simulated incident. Press C or the cancellation button before expiry. Press R to rearm, then F again and allow the countdown to expire. The displayed outcome is simulated; this demonstration never sends a real message. There is no prerecorded prompt yet, so the app shows an audio error while the countdown continues. This slide is an interaction schematic, not an actual application screenshot.')

# 08 — Evidence metric and three meaningful testing categories.
e = [text('07  /  EVIDENCE SO FAR',96,82,1400,26,'muted',bold=True),
     text('43',82,210,780,340,'ink','Space Grotesk',True,h=420),
     stack(105,660,770,[t('automated tests passing',48,'ink',True,'Space Grotesk'),
                        t('Application behavior.\nNot detection accuracy.',38,'muted')],gap=28)]
for y,n,title,sub in [(260,'01','Timing boundaries','Cancellation, expiry, deduplication, rearming.'),
                       (475,'02','Fault handling','Camera loss, missing pose, worker failures.'),
                       (690,'03','Transport isolation','Timeouts, uncertain delivery, simulation guard.')]:
    e += [stack(1010,y,780,[t(n,26,'muted',True),t(title,48,'ink',True,'Space Grotesk'),t(sub,34,'muted',h=98)],gap=14)]
e += [text('Source: prototype test suite at application commit 1d72e63',105,950,1680,24,'muted')]
add('08 / Evidence and limits','ivory',e,
    'All 43 tests passed with optional UI/audio dependencies installed. In a fresh environment with no third-party packages, 41 passed and two optional decoding/rendering tests skipped. An actual OpenCV window completed a simulated countdown and simulated delivery. No real speaker playback, camera/model operation, or Telegram destination was tested. Do not translate this test count into a detection accuracy claim.')

# 09 — Planned empirical evaluation; metrics have no fabricated values.
e = [text('08  /  THE NEXT EXPERIMENT',96,82,1500,26,'muted',bold=True),
     text('Test the detector.\nThen test the whole system.',90,180,1740,96,'ink','Space Grotesk',True)]
for x,y,n,title,body in [(96,500,'01','Verify inputs','Resolve checkpoint preprocessing\nand output class meaning.'),
                        (690,590,'02','Evaluate clips','Falls, sitting, bending,\nintentional lying down.'),
                        (1280,680,'03','Verify hardware','Webcam, recorded prompt,\nTelegram and internet failure.')]:
    e += [rect(x,y,500,12,'orange'),stack(x,y+35,500,[t(n,30,'muted',True),t(title,44,'ink',True,'Space Grotesk'),t(body,32,'muted')],gap=18)]
e += [text('Record: detected falls · missed falls · false alerts · alert delay',98,940,1720,34,'ink')]
add('09 / Planned evaluation','ivory',e,
    'These are planned experiments; no detection measurements are available yet. Use appropriately licensed existing fall clips and representative non-fall activities; do not stage dangerous falls or record monitored webcam footage. Track observed detections, misses, false alerts, and delay. Evaluate camera failure and offline delivery explicitly. A small evaluation cannot establish validated accuracy. Real prompt audio and a Telegram test bot/chat are still needed.')

# 10 — Closing thesis, honest readiness summary, restrained final question.
e = [text('09  /  WHAT WE ARE BUILDING',96,82,1500,26,'mint',bold=True),
     text('Privacy by design.\nVerification by evidence.',90,220,1750,112,'ivory','Space Grotesk',True),
     stack(105,615,790,[t('BUILT',28,'mint',True),t('Safety workflow + local controls\nAudio and notification workers',40,'ivory')],gap=22),
     stack(1050,615,750,[t('NEXT',28,'mint',True),t('Live detection + model evaluation\nRecorded prompt + Telegram setup',40,'ivory')],gap=22),
     text('Experimental prototype. Not a sole emergency response system.',105,870,1700,34,'light'),
     text('Questions?',105,935,1600,38,'mint',bold=True)]
add('10 / Closing','dark',e,
    'Close with the central contribution: a privacy-first architecture paired with an explicit, testable incident workflow. The application layer is implemented; live detection and real-device verification remain. This is a laptop/desktop prototype, not a medically validated system or a substitute for emergency care. Raspberry Pi deployment is out of scope. Invite questions about the preprocessing gate, cancellation policy, and evaluation design.')

# Portable, editable PowerPoint; Figma consumes the same semantic scene graph.
prs = Presentation()
prs.slide_width, prs.slide_height = Inches(13.333333), Inches(7.5)
prs.core_properties.title = 'Fall Detection — Privacy-first desktop prototype'
prs.core_properties.subject = 'Hackathon and science fair presentation'
prs.core_properties.author = 'Fall Detection Project'
scale = prs.slide_width / 1920

def pos(v):
    return int(round(v * scale))

def rgb(value):
    return RGBColor.from_string(value)

def draw(s, element, ox=0, oy=0):
    kind=element['type']
    x,y=element['x']+ox,element['y']+oy
    if kind=='stack':
        if element['color']:
            draw(s,rect(x,y,element['w'],element['h'],element['color'],14))
        child_y=y+element['padding']
        for child in element['children']:
            child=dict(child,x=x+element['padding'],y=child_y)
            draw(s,child)
            child_y+=child['h']+element['gap']
        return
    if kind=='text':
        shape=s.shapes.add_textbox(pos(x),pos(y),pos(element['w']),pos(element['h']))
        tf=shape.text_frame
        tf.clear()
        tf.word_wrap=True
        tf.auto_size=MSO_AUTO_SIZE.NONE
        tf.margin_left=tf.margin_right=tf.margin_top=tf.margin_bottom=0
        tf.vertical_anchor=MSO_ANCHOR.TOP
        for i,value in enumerate(element['text'].split('\n')):
            paragraph=tf.paragraphs[0] if i==0 else tf.add_paragraph()
            paragraph.text=value
            paragraph.font.name=element['font']
            paragraph.font.size=Pt(element['size']/2)
            paragraph.font.bold=element['bold']
            paragraph.font.color.rgb=rgb(element['color'])
            paragraph.line_spacing=1.04
            paragraph.space_before=paragraph.space_after=Pt(0)
        return
    if kind=='line':
        shape=s.shapes.add_connector(MSO_CONNECTOR.STRAIGHT,pos(x),pos(y),pos(element['x2']+ox),pos(element['y2']+oy))
        shape.line.color.rgb=rgb(element['color'])
        shape.line.width=Pt(element['thickness']/2)
        for effect in shape._element.xpath('.//a:effectRef'):
            effect.set('idx', '0')
        return
    shape_type=MSO_SHAPE.OVAL if kind=='ellipse' else (MSO_SHAPE.ROUNDED_RECTANGLE if element.get('radius') else MSO_SHAPE.RECTANGLE)
    shape=s.shapes.add_shape(shape_type,pos(x),pos(y),pos(element['w']),pos(element['h']))
    shape.fill.solid()
    shape.fill.fore_color.rgb=rgb(element['color'])
    shape.line.fill.background()
    for effect in shape._element.xpath('.//a:effectRef'):
        effect.set('idx', '0')
    if shape_type==MSO_SHAPE.ROUNDED_RECTANGLE:
        shape.adjustments[0]=0.04

for scene in slides:
    s=prs.slides.add_slide(prs.slide_layouts[6])
    s.background.fill.solid()
    s.background.fill.fore_color.rgb=rgb(scene['background'])
    for element in scene['elements']:
        draw(s,element)
    s.notes_slide.notes_text_frame.text=scene['notes']
prs.save(OUT/'Fall-Detection.pptx')
(OUT/'slides.json').write_text(json.dumps(slides,ensure_ascii=False,indent=2))
(OUT/'speaker-notes.md').write_text('# Speaker notes\n\n'+'\n\n'.join(f'## {s["name"]}\n\n{s["notes"]}' for s in slides)+'\n')
print(f'Built {len(slides)} editable slides.')
