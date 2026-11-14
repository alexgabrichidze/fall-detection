# Recorded prompts (Person B)

The application accepts an operator-supplied recording saying "Are you okay?".
No prompt asset is included yet. Use 16-bit PCM WAV, mono or stereo, at most
10 seconds, and pass its path with `--prompt /absolute/path/prompt.wav`.

Until the recording is supplied, countdowns and cancellation work and the
application displays a missing-prompt error. Playback runs on a worker and does
not capture microphone input. Verify audibility on the development computer.
Do not add recordings of a monitored person or personal microphone captures.
