# My vadeio encoda

this is my attempt at encoding in a multi pc setup, currently supports svtav1, altho aomenc/its forks could be added in
like two lines _(get real, the efficiency from cpu0-tuneLavish-sb-qp-sweep1 aomenc is hot air stop using ur cpu as a
space heater)_

![lovely screenshot of th eworker](Screenshotidfk.png)

# Getting started
# **OUT OF DATE READ THE CODE!**

guide to get started (NOT COMPLETE, YOU MAY NEED TO INSTALL MORE PACKAGES, TELL ME IF YOU DO):

before you start: you need to have docker installed and working [https://docs.docker.com/engine/install/]()

1. Install requirements ```pip install -r requirements.txt```
2. Create `temp` dir (```mkdir temp```)
3. If you only want to encode on a single system, skip to step 8 and edit `DontUseCelery` to True in `main.py`
4. Create nfs file share pointing at temp dir
    - manual way for ubuntu: `sudo apt install nfs-kernel-server` -> `sudo nano /etc/exports` ->
      put `/point/at/the/temp/dir *(rw,sync,no_subtree_check,no_root_squash)` at the
      bottom -> `sudo exportfs -a` -> `sudo systemctl restart nfs-server`
    - or using a docker container, sample command in `nfsMountDocker` just swap the dir to temp_dir_full_path
5. Run redis
    - i recommend just ```docker run -d -p 6379:6379 --name videoEncoderRedis redis```
6. Build the docker image ```DOCKER_BUILDKIT=1 docker build -t vadeio-encoda .```
    - if you want a multi pc setup just to guide bellow now
7. Run the docker image
    - example in `runTheDocker`. Swap `NFS_SERVER` and `REDIS_HOST` to ip_of_the_master e.g. 192.168.1.2, `NFS_MNT` to
      temp_dir_full_path
8. Edit `main.py`,
    - scroll to bottom, in there you provide a input file nad few variables to change
    - will be cli in the near future
9. run ```REDIS_HOST=ip_of_the_master python3 main.py```
10. it will create a scene cache (can hang for high-resolution) and then start encoding on the worker[s]
11. after encoding is done, we check for errors+merge
    - run ```python3 concat.py output.mp4```, it will tell us if any files are broken, if so remove them and go back to
      step 9
12. BOOM you have a video, you can mux it with audio etc etc

## Multi PC Setup

in the case that you want to do this on multiple pc's

1. transfer the docker container and run it
    - after step 6 of above guide, do ```docker save vadeio-encoda:latest | gzip > ./myimage_latest.tar.gz```
    - on x pcs:
        - transfer the tar ball `myimage_latest.tar.gz`
        - load the image ```docker load < myimage_latest.tar.gz```
        - do step 7 of above guide
2. repeat on as many pc's as you want, just make sure they can reach each-other
3. after all the docker images are running, go back to step 8 of the above guide

## Notes

- if you already are running `main.py`, you can spin up new workers (multi pc guide), they will automatically connect
  and split the
  workload
- if you crash/abort the script, dont worry, just rerun it with the same arguments and it will pick up where it left
  off (there can be corrution but `concat.py` will take care of that)
- i *personally* did lots of testing and my ffmpeg split/concat method is frame perfect, but if you find any issues,
  please provide a sample and create a issue
- ~~the amount of simultaneous chunks per worker is 8, you can change it in `ThaVaidioEncoda.py` line 19 "
  worker_concurrency" (automagic way coming soon tm)~~ scales based on load avg and free memory (acually its brocken for now)

## TODO

- [ ] make the docker image smaller (its 4gb lmao)
- [ ] add support for more encoders (easy but idrc)
- [X] split the ThaVaidioEncoda.py into multiple files
- [X] Auto muxing & auto encoding
- [ ] Auto retry failed chunks & auto merge
- [X] add cli interface to main.py
- [X] dynamically select worker_concurrency based on load average
- [ ] alternative to nfs file for worker sharing
- [ ] fix non ideal scene splits, improve scene detection/keyframe placement

### far future

- [ ] auto bitrate_lader_selection/convex_hull_enocding for extra ~10% efficiency
- [ ] auto grain synthesis (50% done, needs more testing)
- [ ] auto param tuner

# general design

### vertical scaling

For a long time, encoders were simple enough that more cpu = faster, then av1 came. the encoding process contained so
many dependencies that more threads just didn't help the speed after a certain point.
ofc encoders like SvtAv1 can pin all threads but that's only on high presets, we eventually run into a wall where we
have some dependencies and cant parallelize the encoding more.
So we had to start thinking about "horizontal scaling"

### encoding and horizontal scaling

video encoding doesn't scale horizontally, that's why splitting the video into chunks and encoding each chunk
independently is popular,

### why not just split the video into chunks and encode them in parallel

Just spliting the video every, lets say 4s seconds might result in bad keyframe placment tho. Since one shot contains a
lot of temporal redundancy and just putting a I frame in the middle of a long shot is wasted space.

### Shot-based splitting

Thats why projects like av1an/this/Netflix'es encoding pipeline split the video based on shots so we can put a single I
frame at the begging and save a lot of space. Think a long zoom out shot, the frames look almost the same for multiple
seconds aka free gains. Also, allowing us to use open gop

### Why not just use Av1an

- Well first of all it was rewritten in rust, and while rust great n all: 1. I don't rust lmao 2. You don't need a
  memory safe, compiled language to execute some shell commands in order... get real
- It uses vapoursynth, which is a great, but for some reason it doesn't work on my pc (i tried)
- It only works on a single pc, so the advantage of infinite horizontal scaling possible by splitting is constrained to
  only one system

## Credits

all the people i stole code from, and the people who made the tools i used