# wake/noise4

Noise alone for the wake (KEHOACH 3.11): python -m srpipe.tasks.wake.data noise-split.
Each clip's speaker column is the recording it was cut from, its room column its pool.
Roles {'val': 0.1, 'test': 0.1} of the recordings by seed 20261121, train the rest; screening's rejects
and clips labelled with a class of the noise block's exclude list are left out.
FSD50K clips only under the licences of the noise block, none labelled with a class of its fsd_exclude,
none whose Freesound id a DNS clip holds.
A pool with a cut lists <item>@<start>-<end> spans of its recordings by the noise block's cuts;
a pool with max_hours keeps that many hours of them, drawn by the seed.

- door: train_noise 4525, val_noise 572, test_noise 481
- squeak: train_noise 739, val_noise 124, test_noise 92
- dragging: train_noise 63, val_noise 2, test_noise 9
- typing: train_noise 1024, val_noise 163, test_noise 135
- munching: train_noise 225, val_noise 4, test_noise 24
- breath: train_noise 430, val_noise 31, test_noise 55
- copy_machine: train_noise 65, val_noise 0, test_noise 13
- friction: train_noise 157, val_noise 22, test_noise 20
- footsteps: train_noise 322, val_noise 38, test_noise 38
- kitchen: train_noise 5, val_noise 2, test_noise 2
- saw: train_noise 492, val_noise 71, test_noise 53
- tools: train_noise 736, val_noise 92, test_noise 96
- knock: train_noise 364, val_noise 48, test_noise 49
- beeps: train_noise 864, val_noise 108, test_noise 101
- chimes: train_noise 742, val_noise 100, test_noise 103
- appliances: train_noise 589, val_noise 74, test_noise 78
- desk: train_noise 2646, val_noise 293, test_noise 331
- furniture: train_noise 1393, val_noise 170, test_noise 164
- kitchen_fsd: train_noise 1639, val_noise 210, test_noise 227
- water: train_noise 1302, val_noise 148, test_noise 145
- body: train_noise 1732, val_noise 217, test_noise 218
- laugh: train_noise 685, val_noise 90, test_noise 73
- pets: train_noise 637, val_noise 84, test_noise 88
- ringing: train_noise 1552, val_noise 182, test_noise 194
- machines: train_noise 894, val_noise 118, test_noise 111
- music: train_noise 2213, val_noise 346, test_noise 276
- tv: train_noise 426, val_noise 0, test_noise 0
- qut_kitchen: train_noise 550, val_noise 0, test_noise 0
- home: train_noise 2587, val_noise 521, test_noise 397
- fan: train_noise 1296, val_noise 154, test_noise 148
- musan: train_noise 662, val_noise 82, test_noise 77
- musan_bible: train_noise 69, val_noise 9, test_noise 7

- train_noise.txt: 2bd997224291ca83da03cc7db38a6af002972e9ea311481420ca8e7e8af9ba06
- val_noise.txt: a9698bf979a4a2725684e04c3cc97021f0ee98dc902db705ebaeff132251cefd
- test_noise.txt: 598d7d94f1293763463c7edd80b291173f10abe005f8c580fa63213a06ddf450
