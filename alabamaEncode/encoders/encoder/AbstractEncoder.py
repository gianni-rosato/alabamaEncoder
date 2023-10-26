import copy
import json
import os
import time
from abc import abstractmethod, ABC
from typing import List

from alabamaEncode.encoders.RateDiss import RateDistribution
from alabamaEncode.encoders.encodeStats import EncodeStats, EncodeStatus
from alabamaEncode.sceneSplit.ChunkOffset import ChunkObject
from alabamaEncode.utils.binary import doesBinaryExist
from alabamaEncode.utils.execute import syscmd
from alabamaEncode.utils.ffmpegUtil import (
    get_total_bitrate,
    get_video_vmeth,
    get_video_ssim,
)


class AbstractEncoder(ABC):
    """
    owo
    """

    chunk: ChunkObject = None
    temp_folder: str
    bitrate: int = None
    crf: int = None
    current_scene_index: int
    passes: int = 2
    video_filters: str = ""
    output_path: str
    speed = 4
    threads = 1
    rate_distribution: RateDistribution = (
        RateDistribution.CQ
    )  # :param mode: 0:VBR 1:CQ 2:CQ VBV 3:VBR VBV
    qm_enabled = False
    grain_synth = 10
    qm_min = 8
    qm_max = 15
    max_bitrate = 0
    override_flags: str = ""

    bit_override = 10

    svt_bias_pct = 50  # 100 vbr like, 0 cbr like
    svt_open_gop = True
    keyint: int = -1
    svt_sdc: int = 0
    svt_chroma_thing = -2
    svt_supperres_mode = 0
    svt_superres_denom = 8
    svt_superres_kf_denom = 8
    svt_superres_qthresh = 43
    svt_superres_kf_qthresh = 43
    svt_sframe_interval = 0
    svt_sframe_mode = 2
    svt_cli_path = "SvtAv1EncApp"
    svt_tune = 0  # tune for PsychoVisual Optimization by default
    svt_tf = 1  # temporally filtered ALT-REF frames
    svt_overlay = 0  # enable overlays
    film_grain_denoise: (0 | 1) = 1

    color_primaries = "bt709"
    transfer_characteristics = "bt709"
    matrix_coefficients = "bt709"
    maximum_content_light_level = ""
    maximum_frame_average_light_level = ""

    running_on_celery = False

    def setup(self, chunk: ChunkObject, config):
        self.update(
            chunk=chunk,
            temp_folder=config.temp_folder,
            bitrate=config.bitrate,
            crf=config.crf,
            current_scene_index=chunk.chunk_index,
            passes=config.passes,
            video_filters=config.video_filters,
            output_path=chunk.chunk_path,
            speed=config.speed,
            grain_synth=config.grain_synth,
            rate_distribution=config.rate_distribution,
            threads=config.threads,
            qm_enabled=config.qm_enabled,
            qm_min=config.qm_min,
            qm_max=config.qm_max,
            content_type=config.content_type,
            override_flags=config.override_flags,
            color_primaries=config.color_primaries,
            transfer_characteristics=config.transfer_characteristics,
            matrix_coefficients=config.matrix_coefficients,
            maximum_content_light_level=config.maximum_content_light_level,
            maximum_frame_average_light_level=config.maximum_frame_average_light_level,
        )

    def update(self, **kwargs):
        """
        Update the encoder with new values, with type checking
        """

        # Define a dictionary mapping attribute names to their types
        valid_attr_types = {
            "chunk": ChunkObject,
            "temp_folder": str,
            "bitrate": int,
            "crf": int,
            "current_scene_index": int,
            "passes": int,
            "video_filters": str,
            "output_path": str,
            "speed": int,
            "first_pass_speed": int,
            "grain_synth": int,
            "threads": int,
            "tune": int,
            "rate_distribution": RateDistribution,
            "qm_enabled": bool,
            "qm_min": int,
            "qm_max": int,
            "content_type": str,
            "override_flags": str,
        }

        # Loop over the dictionary
        for attr, attr_type in valid_attr_types.items():
            # If the attribute is present in kwargs
            if attr in kwargs:
                # Get the value of the attribute
                value = kwargs.get(attr)
                # If the value is not an instance of the correct type, raise an Exception
                if not isinstance(value, attr_type):
                    raise Exception(f"FATAL: {attr} must be a {attr_type.__name__}")

        # If temp_folder is in kwargs, and is not a valid directory, raise an Exception
        if "temp_folder" in kwargs and not os.path.isdir(kwargs["temp_folder"]):
            raise Exception(
                f"FATAL: temp_folder ({kwargs['temp_folder']}) must be a valid directory"
            )

        # After all checks, update the attributes
        for attr, value in kwargs.items():
            setattr(self, attr, value)

    def run(
        self,
        override_if_exists=True,
        timeout_value=-1,
        calculate_vmaf=False,
        calcualte_ssim=False,
        vmaf_params=None,
    ) -> EncodeStats:
        """
        :param calcualte_ssim: self-explanatory
        :param calculate_vmaf: self-explanatory
        :param vmaf_params: dict of vmaf params
        :param override_if_exists: if false and file already exist don't do anything
        :param timeout_value: how much (in seconds) before giving up
        :return: EncodeStats object with scores bitrate & stuff
        """
        stats = EncodeStats()

        for command in self.get_needed_path():
            if not doesBinaryExist(command):
                raise Exception(f"Could not find {command} in path")

        if os.path.exists(self.output_path) and not override_if_exists:
            stats.status = EncodeStatus.DONE
        else:
            if self.chunk.path is None or self.chunk.path == "":
                raise Exception("FATAL: output_path is None or empty")

            if not os.path.exists(self.chunk.path):
                raise Exception("FATAL: input file does not exist")
            if self.chunk is None:
                raise Exception("FATAL: chunk is None")
            if self.chunk.chunk_index is None:
                raise Exception("FATAL: current_scene_index is None")

            original_path = copy.deepcopy(self.output_path)

            if self.running_on_celery:
                temp_celery_path = "/tmp/celery/"
                os.makedirs(temp_celery_path, exist_ok=True)
                self.output_path = f"{temp_celery_path}{self.chunk.chunk_index}{self.get_chunk_file_extension()}"

            out = []
            start = time.time()
            commands = self.get_encode_commands()

            if self.running_on_celery:
                commands.append(f"cp {self.output_path} {original_path}")
                commands.append(f"rm {self.output_path} {self.output_path}.stat")

            self.output_path = original_path

            for command in commands:
                output = syscmd(command, timeout_value=timeout_value)
                out.append(output)

            stats.time_encoding = time.time() - start

            if (
                not os.path.exists(self.output_path)
                or os.path.getsize(self.output_path) < 100
            ):
                stats.status = EncodeStatus.FAILED
                print("Encode command failed, output:")
                for o in out:
                    if isinstance(o, str):
                        o = o.replace("\x08", "")
                        print(o)
                print("Commands: ")
                for c in self.get_encode_commands():
                    print(c)

                raise Exception("FATAL: ENCODE FAILED FILE NOT FOUND OR TOO SMALL")

            if stats.time_encoding < 1:
                stats.time_encoding = 1
                print("WARINING: ENDODING TIME LESS THEN A SECOND, setting to 1")

            stats.status = EncodeStatus.DONE

        if calculate_vmaf:
            # deconstruct vmaf_params and pass them to get_video_vmeth
            if vmaf_params is None:
                vmaf_params = {}

            uhd_model = vmaf_params.get("uhd_model", False)
            disable_enchancment_gain = vmaf_params.get(
                "disable_enchancment_gain", False
            )

            threads = vmaf_params.get("threads", 1)

            # log_path = vmaf_params.get("log_path", None)

            log_path = self.output_path + ".vmaflog"

            try:
                stats.vmaf = get_video_vmeth(
                    self.output_path,
                    self.chunk,
                    video_filters=self.video_filters,
                    uhd_model=uhd_model,
                    disable_enchancment_gain=disable_enchancment_gain,
                    log_path=log_path,
                    threads=threads,
                )

                try:
                    with open(log_path) as f:
                        vmaf_scores_obj = json.load(f)
                    frames = []

                    for frame in vmaf_scores_obj["frames"]:
                        frames.append([frame["frameNum"], frame["metrics"]["vmaf"]])

                    frames.sort(key=lambda x: x[0])

                    # calc 1 5 10 25 50 percentiles
                    vmaf_scores = [x[1] for x in frames]
                    vmaf_scores.sort()
                    stats.vmaf_percentile_1 = vmaf_scores[int(len(vmaf_scores) * 0.01)]
                    stats.vmaf_percentile_5 = vmaf_scores[int(len(vmaf_scores) * 0.05)]
                    stats.vmaf_percentile_10 = vmaf_scores[int(len(vmaf_scores) * 0.1)]
                    stats.vmaf_percentile_25 = vmaf_scores[int(len(vmaf_scores) * 0.25)]
                    stats.vmaf_percentile_50 = vmaf_scores[int(len(vmaf_scores) * 0.5)]
                    stats.vmaf_avg = sum(vmaf_scores) / len(vmaf_scores)

                except Exception as e:
                    print(e)
                    print("Failed to get vmaf percentiles")

            except Exception as e:
                print(e)
                print("Failed to get vmaf")
        if calcualte_ssim:
            stats.ssim = get_video_ssim(
                self.output_path, self.chunk, video_filters=self.video_filters
            )

        stats.size = os.path.getsize(self.output_path) / 1000
        stats.bitrate = int(get_total_bitrate(self.output_path) / 1000)

        return stats

    @abstractmethod
    def get_encode_commands(self) -> List[str]:
        """
        Abstract method overriden by encoders.
        :return: A list of cli commands to encode, according to class fields
        """
        pass

    @abstractmethod
    def get_needed_path(self) -> List[str]:
        """
        return needed path items for encoding eg `aomenc` or `SvtAv1EncApp`
        """
        return ["ffmpeg", "ffprobe"]

    def get_ffmpeg_pipe_command(self) -> str:
        """
        return cli command that pipes a y4m stream into stdout using the chunk object
        """
        return self.chunk.create_chunk_ffmpeg_pipe_command(
            video_filters=self.video_filters,
            bit_depth=self.bit_override,
        )

    @abstractmethod
    def get_chunk_file_extension(self) -> str:
        return ".mkv"
