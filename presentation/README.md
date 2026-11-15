# Fall Detection — hackathon / science fair presentation

Ten widescreen slides, designed for an approximately five-minute project talk.

- **Fall-Detection.pptx**: editable PowerPoint, with speaker notes on all ten slides.
- **Fall-Detection.pdf**: presentation copy with embedded fonts; use this for
  consistent typography on a different computer.
- **speaker-notes.md**: the same presenter cues as a standalone document.
- **preview.png**: overview of the slide layouts.
- **fonts/**: Space Grotesk and DM Sans, with their open font licenses. Install
  these fonts before editing or presenting the PowerPoint on another machine.
- **build_deck.py** and **slides.json**: editable source and scene data.
- **DESIGN.md**: content plan, visual direction, and evidence boundaries.

Editable Figma Slides version:
https://www.figma.com/slides/BFE7Mx24PUafIuIbGqToaW

The deck distinguishes implemented application behavior, simulated demonstrations,
and pending live detection/model and hardware validation. It contains no invented
detection accuracy, medical statistics, or fabricated experimental results.
Evidence snapshot: application commit 1d72e63 and the observed 43-test run.

To demonstrate the actual application alongside slide seven:

```bash
.venv/bin/fall-detection --detector fake --ui --scenario idle
```

Press F, then cancel with C. Press R, then F again and let the countdown expire.
The application currently has no prerecorded prompt; the missing-audio status is
expected. All detections and deliveries in this command are simulated.

The PowerPoint can be rebuilt using a separate Python tools environment with
python-pptx 1.0.2 installed, then running `python presentation/build_deck.py`.
PDF export was checked through LibreOffice. Application dependencies are unchanged.
