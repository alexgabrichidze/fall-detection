# Fall Monitor presentation

A ten-slide, speaker-led presentation for a hackathon or science fair, styled in Quiet Precision: pale green, dark ink, Instrument Sans, and IBM Plex Mono.

Open `index.html` directly in a browser. Fonts, diagrams, styles, and JavaScript are embedded, so the presentation works offline. `fall-monitor.pdf` is the static widescreen version for sharing or printing.

## Present

- Use arrow keys, Space, Page Up/Down, or swipe to navigate.
- Move the pointer to reveal navigation and the fullscreen button.
- Press **N** to show or hide speaker notes.
- Slide 9 contains an interactive countdown. Click **Start walkthrough**, then **I'm okay**, or let the countdown reach the alert stage. This diagram does not access the camera, microphone, or Telegram.

## Edit

Press **E**, or hover near the top-left corner, to enable text editing. Click text to change it. Changes are saved in this browser's local storage. Press **Ctrl+S** or **Cmd+S** to download a portable HTML copy with your edits.

The `:root` CSS variables control colors and typography. The `.reveal` class controls entrance animation. Each slide uses a fixed 1920 × 1080 stage that scales uniformly to the screen. The PDF preserves the final slide appearance without animation or the interactive walkthrough.

## Speaker outline

1. **A check-in, when it matters.** Introduce the camera-to-response idea.
2. **Detection is the beginning.** Explain why the project connects recognition to a person.
3. **One continuous flow.** Describe observation, recognition, a check-in, and the two response branches.
4. **Movement becomes a signal.** Explain the 17 selected landmarks and 30-frame Transformer input.
5. **Intelligence stays in the room.** Show the local processing boundary and text-only caregiver notification.
6. **I'm okay.** Introduce optional offline voice cancellation and the existing button and keyboard controls.
7. **A simple message.** Explain the caregiver connection through Telegram.
8. **Three modules.** Present detection, safety, and device controls inside one Python application.
9. **A moment to respond.** Walk through the interactive countdown.
10. **Local intelligence. Human connection.** Close and invite questions or switch to the application.

## Content and asset sources

Project behavior and technical details come from the repository's `README.md`, `models/README.md`, and the detection, preprocessing, safety, audio, voice, notification, and device-control modules. The 20-second response window is the configurable default. The 17 landmarks and 30 frames describe model inputs. The Telegram quotation is an excerpt of the application's notification text. Body diagrams are schematic vector graphics, not camera footage.

Typography: [Instrument Sans](https://fonts.google.com/specimen/Instrument+Sans) and [IBM Plex Mono](https://fonts.google.com/specimen/IBM+Plex+Mono), distributed under the SIL Open Font License. Presentation structure follows the installed [Frontend Slides skill](https://github.com/zarazhangrui/frontend-slides).
