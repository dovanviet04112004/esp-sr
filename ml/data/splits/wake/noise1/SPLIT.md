# wake/noise1

Noise alone for the wake (KEHOACH 3.11): python -m srpipe.tasks.wake.data noise-split.
Each clip's speaker column is the recording it was cut from, its room column its pool.
Roles {'val': 0.1, 'test': 0.1} of the recordings by seed 20261113, train the rest; screening's rejects
and clips labelled with a class of the noise block's exclude list are left out.

- door: train_noise 4530, val_noise 437, test_noise 725
- squeak: train_noise 711, val_noise 128, test_noise 128
- dragging: train_noise 57, val_noise 8, test_noise 9
- saw: train_noise 686, val_noise 83, test_noise 74
- tools: train_noise 770, val_noise 95, test_noise 96
- knock: train_noise 407, val_noise 61, test_noise 55
- fan: train_noise 1351, val_noise 180, test_noise 68
- musan: train_noise 676, val_noise 78, test_noise 88

- train_noise.txt: 261aa5c6262f5d5ac7c1cb008261cd3893ea2c92d7830bc00954921b7605be67
- val_noise.txt: 8315e594cabefe2fbb71da5a853b520ad8b2e986b2614f0fd4d6af78f2ec17a3
- test_noise.txt: c3b8f37f2ce32fe884fadc401bc2742bc996d9549d697a9e8ca9ec1a3b850ba1
