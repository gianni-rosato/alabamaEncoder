"""
Testing vbr auto bitrate vs crf auto bitrate
"""
import os

from alabamaEncode.encoders.encoderImpl.Svtenc import AbstractEncoderSvtenc

from alabamaEncode.encoders.RateDiss import RateDistribution
from alabamaEncode.sceneSplit.ChunkOffset import ChunkObject

if __name__ == "__main__":
    paths = [
        "/mnt/data/liveAction_normal.mp4",
        "/mnt/data/liveAction_highMotion.mkv",
        "/mnt/data/liveaction_bright.mkv",
    ]

    for path in paths:
        print(f"\n\n## Doing: {path}")
        chunk = ChunkObject(path=path, first_frame_index=0, last_frame_index=200)

        test_env = "./tstCRF" + path.split("/")[-1].split(".")[0] + "/"
        # shutil.rmtree(test_env, ignore_errors=True)
        if not os.path.exists(test_env):
            os.mkdir(test_env)

        crope_stringe = ""

        if "liveAction_normal" in path:
            # clip is 4k but we only want to encode 1080p, also map from hdr
            crope_stringe = "crop=3808:1744:28:208,scale=-2:1080:flags=lanczos,zscale=t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,tonemap=tonemap=reinhard:desat=0,zscale=t=bt709:m=bt709:r=tv"
        elif "liveAction_highMotion" in path:
            # crop black borders
            crope_stringe = "crop=1920:800:0:140"
        elif "liveAction_4k" in path:
            # the same clip as liveAction_normal but in we dont scale down to 1080p
            crope_stringe = "crop=3808:1744:28:208,zscale=t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,tonemap=tonemap=reinhard:desat=0,zscale=t=bt709:m=bt709:r=tv"
        elif "liveaction_bright" in path:
            crope_stringe = "crop=1920:960:0:60"

        svtenc = AbstractEncoderSvtenc()

        svtenc.update()
        svtenc.svt_chroma_thing = 0
        svtenc.keyint = -2
        svtenc.svt_bias_pct = 50
        svtenc.svt_open_gop = True

        svtenc.update(
            rate_distribution=RateDistribution.CQ,
            crf=18,
            passes=1,
            chunk=chunk,
            current_scene_index=0,
            threads=12,
            video_filters=crope_stringe,
            grain_synth=3,
        )

        if "liveAction_normal" in path:
            svtenc.update(bitrate=1000)
        elif "liveAction_highMotion" in path:
            svtenc.update(bitrate=2000)
        elif "Animation" in path:
            svtenc.update(bitrate=1500)
        elif "stopmotion" in path:
            svtenc.update(bitrate=3000)
        elif "liveAction_4k" in path:
            svtenc.update(bitrate=4000)
        elif "liveaction_bright" in path:
            svtenc.update(bitrate=1000)

        print(f"CRF {svtenc.crf}\n")
        print(
            f"| _tune_ |  time taken  | kpbs | vmaf  | BD Change % | time Change % |\n"
            "|----------|:------:|:----:|:-----:|:-----------:|:---:|"
        )
        control_dbrate = -1
        control_time = -1
        for tune in [1, 0, 2]:
            svtenc.update(output_path=f"{test_env}tune_{tune}.ivf")
            svtenc.svt_tune = tune
            svtenc.bit_override = 8
            if tune == 2:
                svtenc.svt_cli_path = (
                    "/home/kokoniara/dev/BlueSwordM-SVT-AV1/Bin/Release/SvtAv1EncApp"
                )
            else:
                svtenc.svt_cli_path = "SvtAv1EncApp"

            print("command: ", svtenc.get_encode_commands())
            quit()
            stats = svtenc.run(override_if_exists=False, calculate_vmaf=True)
            stats.time_encoding = round(stats.time_encoding, 2)

            curr_db_rate = stats.size / stats.vmaf
            if tune == 1:
                control_time = stats.time_encoding
                control_dbrate = curr_db_rate

            change_from_zero = (curr_db_rate - control_dbrate) / control_dbrate * 100
            change_from_zero = round(change_from_zero, 2)
            change_from_zero_time = (
                (stats.time_encoding - control_time) / control_time * 100
            )
            change_from_zero_time = round(change_from_zero_time, 2)
            print(
                f"| {tune} |"
                f" {stats.time_encoding}s |"
                f" {stats.bitrate} "
                f"| {round(stats.vmaf, 2)} | "
                f" {change_from_zero}% |"
                f" {change_from_zero_time}% |"
            )
