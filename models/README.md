# Fall classifier

The project uses the 30-frame Transformer checkpoint from Payut Charoensri's MIT-licensed Fall-Detection repository. [Checkpoint and source](https://github.com/punpayut/Fall-Detection/blob/087d88dfa9fb784cda13379157f983950974f616/deployment/raspberry_pi/fall_detection_transformer.tflite) · [MIT license](LICENSE-punpayut.txt)

Download the checkpoint once from the project directory if `models/fall_detection_transformer.tflite` is missing.

```bash
curl -fL -o models/fall_detection_transformer.tflite \
  https://raw.githubusercontent.com/punpayut/Fall-Detection/087d88dfa9fb784cda13379157f983950974f616/deployment/raspberry_pi/fall_detection_transformer.tflite
```

SHA-256 `c66659c3ac6d37c60469bee4d6f88cb2ac4976928bde6d46b19e7f851e8980b0`. Both upstream deployments ship these bytes. LiteRT verified `float32[1,30,51]` input and `float32[1,1]` output. The sigmoid returns the score for class 1 (fall).

The training notebook interleaves `(x, y, visibility)` for 17 alphabetically sorted landmark names. The extractor uses pixel coordinates and the native indices recorded in `preprocessing.py`, including its mislabeled face points. The notebook centers at the visible hip midpoint and divides x and y by the vertical shoulder-to-hip distance when that distance is nonzero. [Extractor](https://github.com/punpayut/Fall-Detection/blob/087d88dfa9fb784cda13379157f983950974f616/src/utils/extract_keypoint.py) · [Training notebook](https://github.com/punpayut/Fall-Detection/blob/087d88dfa9fb784cda13379157f983950974f616/src/notebook/fall-detection-transformer-f1-v1.ipynb)

The deployment demo uses normalized image coordinates and anatomical face indices. The older CSV reshape script groups columns by field. The selected training-notebook layout matched all 56 input frames in the author's public `20240912_101331_keypoints.csv` sample and gave the same final-window score as notebook preprocessing. On the four upstream example clips, this layout produced scores above 0.90 on three clips. The grouped-column variant stayed below 0.12 on all four. The exported training arrays were unavailable. [Public keypoints](https://www.kaggle.com/datasets/payutch/fall-video-dataset) · [Example clips](https://github.com/punpayut/Fall-Detection/tree/8978b03cdf028ef5c0b326b5592133f8bf59f211/deployment/raspberry_pi)

The extraction script takes one sample per source frame. It specifies no fixed training frame rate. The example videos run at 23.98 or 30 FPS. Live capture targets 30 FPS, accepts real samples at a comparable cadence, and clears evidence after a 200 ms gap or an unreliable pose. A sequence spanning more than two seconds yields an unknown score.

A possible-fall event requires a score of at least 0.90 and a sustained drop from recently upright posture. A moderate drop requires leaning or a nearly horizontal torso. A large drop can also qualify. Evidence must hold for 180 ms. Thresholds live in `detection_config.py`. Camera placement, occlusion, and slow capture can affect detection.
