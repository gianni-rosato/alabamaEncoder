"""
Testing svtav1 tunes
"""
import os
from copy import deepcopy

from alabamaEncode.encoder.impl.Svtenc import EncoderSvtenc
from alabamaEncode.experiments.util.ExperimentUtil import (
    run_tests_across_range,
    read_report,
)

experiment_name = "Testing SvtAv1 TUNES, speed 4"

test_env = os.getcwd() + "/data/tunes/"
if not os.path.exists(test_env):
    os.makedirs(test_env)

report_path = test_env + experiment_name + " CRF.json"


def analyze():
    df = read_report(report_path)

    print(df)

    enc1_vmaf1percenttile_change_arr = []
    enc1_size_change_arr = []
    enc1_vmaf_change_arr = []
    enc1_ssim_change_arr = []
    enc1_bpv_change_arr = []
    enc1_time_encoding_change_arr = []

    enc2_vmaf1percenttile_change_arr = []
    enc2_size_change_arr = []
    enc2_vmaf_change_arr = []
    enc2_ssim_change_arr = []
    enc2_bpv_change_arr = []
    enc2_time_encoding_change_arr = []

    for key, group in df.groupby("basename"):
        print(key)
        control = 0
        enc1 = 0
        for key_j, group_j in group.groupby("test_group"):
            print(key_j)
            # avg 1%tile vmaf
            score = group_j["vmaf_percentile_1"].mean()
            size_score = group_j["size"].mean()
            vmaf_score = group_j["vmaf"].mean()
            ssim_score = group_j["ssim"].mean()
            time_encoding_score = group_j["time_encoding"].mean()

            # how much bits per vmaf
            bpv_score = group_j["size"].mean() / group_j["vmaf"].mean()

            if key_j == "control":
                control = score
                size_control = size_score
                vmaf_control = vmaf_score
                ssim_control = ssim_score
                bpv_control = bpv_score
                time_encoding_control = time_encoding_score
            elif key_j == "enc1":
                enc1 = score
                size_enc1 = size_score
                vmaf_enc1 = vmaf_score
                ssim_enc1 = ssim_score
                bpv_enc1 = bpv_score
                time_encoding_enc1 = time_encoding_score
            elif key_j == "enc2":
                enc2 = score
                size_enc2 = size_score
                vmaf_enc2 = vmaf_score
                ssim_enc2 = ssim_score
                bpv_enc2 = bpv_score
                time_encoding_enc2 = time_encoding_score

        # show pct of difference between control and enc1

        enc1_vmaf1percenttile_change_arr.append((enc1 - control) / control * 100)
        enc1_size_change_arr.append((size_enc1 - size_control) / size_control * 100)
        enc1_vmaf_change_arr.append((vmaf_enc1 - vmaf_control) / vmaf_control * 100)
        enc1_ssim_change_arr.append((ssim_enc1 - ssim_control) / ssim_control * 100)
        enc1_bpv_change_arr.append((bpv_enc1 - bpv_control) / bpv_control * 100)
        enc1_time_encoding_change_arr.append(
            (time_encoding_enc1 - time_encoding_control) / time_encoding_control * 100
        )

        enc2_vmaf1percenttile_change_arr.append((enc2 - control) / control * 100)
        enc2_size_change_arr.append((size_enc2 - size_control) / size_control * 100)
        enc2_vmaf_change_arr.append((vmaf_enc2 - vmaf_control) / vmaf_control * 100)
        enc2_ssim_change_arr.append((ssim_enc2 - ssim_control) / ssim_control * 100)
        enc2_bpv_change_arr.append((bpv_enc2 - bpv_control) / bpv_control * 100)
        enc2_time_encoding_change_arr.append(
            (time_encoding_enc2 - time_encoding_control) / time_encoding_control * 100
        )

    print(f"enable-overlays.md=1 tests")
    print("ENC1 (TUNE 0)")
    print(
        f"overall VMAF 1%tile avg (positive mean better): {sum(enc1_vmaf1percenttile_change_arr) / len(enc1_vmaf1percenttile_change_arr)}%"
    )
    print(
        f"overall size avg (positive mean worse): {sum(enc1_size_change_arr) / len(enc1_size_change_arr)}%"
    )
    print(
        f"overall VMAF avg (positive mean better): {sum(enc1_vmaf_change_arr) / len(enc1_vmaf_change_arr)}%"
    )
    print(
        f"overall SSIM avg (positive mean better): {sum(enc1_ssim_change_arr) / len(enc1_ssim_change_arr)}%"
    )
    print(
        f"overall Bits Per vmaf avg (positive mean worse): {sum(enc1_bpv_change_arr) / len(enc1_bpv_change_arr)}%"
    )
    print(
        f"overall Time Encoding avg (positive mean worse): {sum(enc1_time_encoding_change_arr) / len(enc1_time_encoding_change_arr)}%"
    )

    print("\nENC2 (TUNE 2)")
    print(
        f"overall VMAF 1%tile avg (positive mean better): {sum(enc2_vmaf1percenttile_change_arr) / len(enc2_vmaf1percenttile_change_arr)}%"
    )
    print(
        f"overall size avg (positive mean worse): {sum(enc2_size_change_arr) / len(enc2_size_change_arr)}%"
    )
    print(
        f"overall VMAF avg (positive mean better): {sum(enc2_vmaf_change_arr) / len(enc2_vmaf_change_arr)}%"
    )
    print(
        f"overall SSIM avg (positive mean better): {sum(enc2_ssim_change_arr) / len(enc2_ssim_change_arr)}%"
    )
    print(
        f"overall Bits Per vmaf avg (positive mean worse): {sum(enc2_bpv_change_arr) / len(enc2_bpv_change_arr)}%"
    )
    print(
        f"overall Time Encoding avg (positive mean worse): {sum(enc2_time_encoding_change_arr) / len(enc2_time_encoding_change_arr)}%"
    )


def test():
    control = EncoderSvtenc()
    control.speed = 4
    control.threads = 12
    control.svt_tune = 1

    enc_test = deepcopy(control)
    enc_test.svt_tune = 0

    enc_test2 = deepcopy(control)
    enc_test.svt_tune = 2

    run_tests_across_range(
        [control, enc_test, enc_test2],
        title=experiment_name,
        test_env=test_env,
        skip_vbr=True,
    )


if __name__ == "__main__":
    # test()
    #
    analyze()
    pass
