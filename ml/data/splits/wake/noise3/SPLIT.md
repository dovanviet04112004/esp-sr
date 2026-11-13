# wake/noise3

Noise alone for the wake (KEHOACH 3.11): python -m srpipe.tasks.wake.data noise-split.
Each clip's speaker column is the recording it was cut from, its room column its pool.
Roles {'val': 0.1, 'test': 0.1} of the recordings by seed 20261113, train the rest; screening's rejects
and clips labelled with a class of the noise block's exclude list are left out.

- door: train_noise 4461, val_noise 647, test_noise 584
- squeak: train_noise 707, val_noise 151, test_noise 109
- dragging: train_noise 60, val_noise 12, test_noise 2
- friction: train_noise 168, val_noise 24, test_noise 13
- footsteps: train_noise 328, val_noise 33, test_noise 41
- kitchen: train_noise 9, val_noise 0, test_noise 0
- saw: train_noise 540, val_noise 62, test_noise 63
- tools: train_noise 754, val_noise 107, test_noise 98
- knock: train_noise 416, val_noise 46, test_noise 61
- beeps: train_noise 899, val_noise 119, test_noise 129
- chimes: train_noise 781, val_noise 97, test_noise 91
- appliances: train_noise 611, val_noise 72, test_noise 69
- fan: train_noise 1349, val_noise 115, test_noise 135
- musan: train_noise 684, val_noise 86, test_noise 72
- musan_bible: train_noise 68, val_noise 10, test_noise 9

- train_noise.txt: b70ab093b071213c9a59c48430fc7e50c3b85a4550fb6c11809040a553ebe94a
- val_noise.txt: fe3f2991c0f78ec8df74679607af0f73e85874369395d0817a0bf75cc685ffdd
- test_noise.txt: 8575e0f5e0ce959e07c30896cd508ae8eeed5f3d44c78d30abffb75c8c440b6d
