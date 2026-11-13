# wake/noise2

Noise alone for the wake (KEHOACH 3.11): python -m srpipe.tasks.wake.data noise-split.
Each clip's speaker column is the recording it was cut from, its room column its pool.
Roles {'val': 0.1, 'test': 0.1} of the recordings by seed 20261113, train the rest; screening's rejects
and clips labelled with a class of the noise block's exclude list are left out.

- door: train_noise 4547, val_noise 541, test_noise 604
- squeak: train_noise 771, val_noise 68, test_noise 128
- dragging: train_noise 67, val_noise 6, test_noise 1
- saw: train_noise 682, val_noise 74, test_noise 87
- tools: train_noise 758, val_noise 113, test_noise 90
- knock: train_noise 429, val_noise 50, test_noise 44
- beeps: train_noise 917, val_noise 117, test_noise 113
- chimes: train_noise 773, val_noise 102, test_noise 94
- appliances: train_noise 581, val_noise 84, test_noise 89
- fan: train_noise 1331, val_noise 136, test_noise 132
- musan: train_noise 683, val_noise 79, test_noise 80
- musan_bible: train_noise 75, val_noise 3, test_noise 9

- train_noise.txt: ef368e5d64d8c05ef8a95e43f8a8b6b14e5b8527a2085a399320ba9c7faa65a9
- val_noise.txt: 91d8d4dceb37804170b009c32667a22af338bb8ddccf5d3db1c28125fbdb4c1c
- test_noise.txt: d0d8ab022dcb41921b8e12b6800bef8f7b49c43f6223267f217fc124810dd6e0
