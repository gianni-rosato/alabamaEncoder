"""
A class that helps us find the best bitrate. Refer to class docstring
"""
import asyncio
import copy
import os
import pickle
import random
import shutil
import time
from concurrent.futures import ThreadPoolExecutor
from math import log
from typing import List, Tuple

from tqdm import tqdm

from alabamaEncode.adaptive.helpers.probe_chunks import (
    get_test_chunks_out_of_a_sequence,
)
from alabamaEncode.core.alabama import AlabamaContext
from alabamaEncode.encoder.encoder import Encoder
from alabamaEncode.encoder.rate_dist import EncoderRateDistribution
from alabamaEncode.encoder.stats import EncodeStats
from alabamaEncode.metrics.calc import calculate_metric
from alabamaEncode.metrics.vmaf.options import VmafOptions
from alabamaEncode.parallelEncoding.command import BaseCommandObject
from alabamaEncode.parallelEncoding.execute_commands import execute_commands
from alabamaEncode.scene.chunk import ChunkObject
from alabamaEncode.scene.sequence import ChunkSequence


class AutoBitrateCacheObject:
    """
    A class that helps us cache the results of the bitrate ladder
    """

    def __init__(self, bitrate: int, ssim_db: float):
        self.bitrate = bitrate
        self.ssim_db = ssim_db


class AutoBitrateLadder:
    """
    When doing VBR encoding, a problem is to figure out what bitrate to target,
    often we just close out eyes and shoot a dart hoping 2Mbps or something is good enough.
    This is my attempt at finding a base bitrate for a given quality level for given content at a given resolution,
     automatically and hopefully better than human intuition.
    Or just use crf nerd.
    """

    def __init__(self, chunk_sequence: ChunkSequence, config: AlabamaContext):
        self.chunk_sequence = chunk_sequence
        self.config: AlabamaContext = config

        self.chunks = get_test_chunks_out_of_a_sequence(
            self.chunk_sequence, self.random_pick_count
        )

        if len(self.chunks) == 0:
            raise Exception("No chunks to probe")

    random_pick_count = 7
    num_probes = 6
    max_bitrate = 5000
    simultaneous_probes = 3
    max_crf = 45

    def delete_best_bitrate_cache(self):
        """
        Delete the cache file for get_best_bitrate
        """
        path = f"{self.config.temp_folder}/adapt/bitrate/cache.pt"
        if os.path.exists(path):
            os.remove(path)

    @staticmethod
    def get_complexity(enc: Encoder, c: ChunkObject) -> Tuple[int, float]:
        _enc = copy.deepcopy(enc)
        _enc.chunk = c
        _enc.speed = 12
        _enc.passes = 1
        _enc.rate_distribution = EncoderRateDistribution.CQ
        _enc.crf = 16
        _enc.threads = 1
        _enc.grain_synth = 0
        _enc.output_path = (
            f"/tmp/{c.chunk_index}_complexity{_enc.get_chunk_file_extension()}"
        )
        stats: EncodeStats = _enc.run()
        formula = log(stats.bitrate)
        # self.config.log(
        #     f"[{c.chunk_index}] complexity: {formula:.2f} in {stats.time_encoding}s"
        # )
        os.remove(_enc.output_path)
        return c.chunk_index, formula

    def calculate_chunk_complexity(self) -> List[Tuple[int, float]]:
        """
        Do fast preset crf encoding on each chunk in self.chunk_sequence to get a complexity score
        :return: the ChunkSequence with complexity scores
        """
        print("Calculating chunk complexity")

        probe_folder = f"{self.config.temp_folder}/adapt/bitrate/complexity"

        # make sure the folder exists
        if not os.path.exists(probe_folder):
            os.makedirs(probe_folder)

        cache_file = f"{probe_folder}/cache.pt"
        if os.path.exists(cache_file):
            try:
                print("Found cache file, reading")
                complexity_scores = pickle.load(open(probe_folder + "cache.pt", "rb"))
                return complexity_scores
            except:
                print("Failed to read cache file, continuing")

        chunk_sequence_copy = copy.deepcopy(self.chunk_sequence)

        encoder_extension = self.config.get_encoder().get_chunk_file_extension()

        for chunk in chunk_sequence_copy.chunks:
            chunk.chunk_path = f"{probe_folder}/{chunk.chunk_index}{encoder_extension}"

        start = time.time()

        commands = [GetComplexity(self, chunk) for chunk in chunk_sequence_copy.chunks]

        loop = asyncio.get_event_loop()
        loop.run_until_complete(
            execute_commands(
                self.config.use_celery,
                commands,
                -1,
                override_sequential=False,
            )
        )

        complexity_scores = [command.complexity for command in commands]

        print(f"Complexity calculation took {time.time() - start} seconds")

        try:
            print("Caching complexity scores")
            pickle.dump(complexity_scores, open(cache_file, "wb"))
        except:
            print("Failed to save complexity scores cache, continuing")

        for chunk in self.chunk_sequence.chunks:
            for index, complexity in complexity_scores:
                if index == chunk.chunk_index:
                    chunk.complexity = complexity

        return complexity_scores

    def get_best_crf_guided(self):
        """
        :return: The best average crf found based on probing a random selection of chunks in the chunk sequence.
        """
        print("Finding best bitrate")
        probe_folder = f"{self.config.temp_folder}/adapt/crf/"

        cache_file = probe_folder + "cache.pt"
        if os.path.exists(cache_file):
            print("Found cache file, reading")
            cutoff_bitrate, avg_best_crf = pickle.load(open(cache_file, "rb"))
            print(
                f"Best avg crf: {avg_best_crf} crf; Cuttoff bitrate: {self.config.cutoff_bitrate} kbps"
            )
            self.config.cutoff_bitrate = cutoff_bitrate
            self.config.prototype_encoder.crf = avg_best_crf
            return

        shutil.rmtree(probe_folder, ignore_errors=True)
        os.makedirs(probe_folder)

        complexity_scores: List[Tuple[int, float]] = self.calculate_chunk_complexity()

        # sort chunks by complexity
        complexity_scores.sort(key=lambda x: x[1])

        # get the 90tile complexity chunks
        n = len(complexity_scores)

        # Calculate 10th percentile (for the lower end)
        p10_index = int(0.1 * n)

        # Calculate 90th percentile (for the upper end)
        p90_index = int(0.9 * n)

        # Your average complexity chunks are those lying between the 10th and 90th percentile
        avg_complex_chunks = [complexity_scores[i] for i in range(p10_index, p90_index)]

        avg_complex_chunks = random.sample(
            avg_complex_chunks, min(10, len(avg_complex_chunks))
        )

        chunks_for_crf_probe = []

        for c in self.chunk_sequence.chunks:
            for chunk in avg_complex_chunks:
                if c.chunk_index == chunk[0]:
                    chunks_for_crf_probe.append(copy.deepcopy(c))

        print(
            f'Probing chunks: {" ".join([str(chunk.chunk_index) for chunk in chunks_for_crf_probe])}'
        )

        encoder_extension = self.config.get_encoder().get_chunk_file_extension()

        # add proper paths
        for i, chunk in enumerate(chunks_for_crf_probe):
            chunk.chunk_index = i
            chunk.chunk_path = f"{probe_folder}{i}{encoder_extension}"

        commands = [GetBestCrf(self, chunk) for chunk in chunks_for_crf_probe]

        asyncio.get_event_loop().run_until_complete(
            execute_commands(
                self.config.use_celery,
                commands,
                self.config.multiprocess_workers,
                override_sequential=False,
            )
        )

        chunk_runs_crfs = [command.best_crf for command in commands]

        avg_best_crf = int(sum(chunk_runs_crfs) / len(chunk_runs_crfs))

        print(
            f"Crf for 80%tile chunks matching {self.config.vmaf}VMAF: {avg_best_crf} crf"
        )

        print("Probing top 5%tile complex chunks for cutoff bitrate")

        # get the top 5% most complex chunks no less than five, unless the number of chunks is less than 5
        top_complex_chunks = complexity_scores[
            -max(10, int(len(complexity_scores) * 0.05)) :
        ]

        # get a random 30% of the top 5% most complex chunks
        random_complex_chunks = random.sample(
            top_complex_chunks, int(len(top_complex_chunks) * 0.30)
        )

        chunks_for_max_probe = []
        for c in self.chunk_sequence.chunks:
            for chunk in random_complex_chunks:
                if c.chunk_index == chunk[0]:
                    chunks_for_max_probe.append(copy.deepcopy(c))

        cutoff_bitrate = self.crf_to_bitrate(avg_best_crf, chunks_for_max_probe)

        print("Saving crf ladder detection cache file")
        pickle.dump((cutoff_bitrate, avg_best_crf), open(cache_file, "wb"))

        self.config.cutoff_bitrate = cutoff_bitrate
        self.config.prototype_encoder.crf = avg_best_crf

    def get_cutoff_bitrate_from_crf(self, crf):
        probe_folder = f"{self.config.temp_folder}/adapt/crf_to_bitrate/"

        if os.path.exists(probe_folder + "cache.pt"):
            try:
                print("Found cache file, reading")
                avg_best = pickle.load(open(probe_folder + "cache.pt", "rb"))
                print(f"Best avg crf: {avg_best} crf")
                return avg_best
            except:
                pass

        shutil.rmtree(probe_folder, ignore_errors=True)
        os.makedirs(probe_folder)

        complexity_scores: List[Tuple[int, float]] = self.calculate_chunk_complexity()

        # sort chunks by complexity
        complexity_scores.sort(key=lambda x: x[1])

        # get the top 5% most complex chunks no less than ten, unless the number of chunks is less than ten
        top_complex_chunks = complexity_scores[
            -max(10, int(len(complexity_scores) * 0.05)) :
        ]

        # get a random 30% of the top 5% most complex chunks
        random_complex_chunks = random.sample(
            top_complex_chunks, int(len(top_complex_chunks) * 0.30)
        )

        chunks_for_max_probe = []
        for c in self.chunk_sequence.chunks:
            for chunk in random_complex_chunks:
                if c.chunk_index == chunk[0]:
                    chunks_for_max_probe.append(copy.deepcopy(c))

        cutoff_bitreate = self.crf_to_bitrate(crf, chunks_for_max_probe)
        self.config.cutoff_bitrate = cutoff_bitreate
        return cutoff_bitreate

    def get_best_bitrate_guided(self) -> int:
        """
        :return: The best average bitrate found based on probing a random selection of chunks in the chunk sequence.
        """
        print("Finding best bitrate")
        probe_folder = f"{self.config.temp_folder}/adapt/bitrate/"

        if os.path.exists(probe_folder + "cache.pt"):
            try:
                print("Found cache file, reading")
                avg_best = pickle.load(open(probe_folder + "cache.pt", "rb"))
                print(f"Best avg bitrate: {avg_best} kbps")
                return avg_best
            except:
                pass

        shutil.rmtree(probe_folder, ignore_errors=True)
        os.makedirs(probe_folder)

        complexity_scores = self.calculate_chunk_complexity()

        # sort chunks by complexity
        complexity_scores.sort(key=lambda x: x[1])

        # get the top 5% most complex chunks no less than five, unless the number of chunks is less than 5
        top_complex_chunks = complexity_scores[
            -max(10, int(len(complexity_scores) * 0.05)) :
        ]

        # get a random 30% of the top 5% most complex chunks

        random_complex_chunks = random.sample(
            top_complex_chunks, int(len(top_complex_chunks) * 0.30)
        )

        chunks = []

        for c in self.chunk_sequence.chunks:
            for chunk in random_complex_chunks:
                if c.chunk_index == chunk[0]:
                    chunks.append(copy.deepcopy(c))

        print(
            f'Probing chunks: {" ".join([str(chunk.chunk_index) for chunk in chunks])}'
        )

        encoder_extension = self.config.get_encoder().get_chunk_file_extension()

        # add proper paths
        for i, chunk in enumerate(chunks):
            chunk.chunk_index = i
            chunk.chunk_path = f"{probe_folder}{i}{encoder_extension}"

        commands = [GetBestBitrate(self, chunk) for chunk in chunks]

        asyncio.get_event_loop().run_until_complete(
            execute_commands(
                self.config.use_celery,
                commands,
                self.config.multiprocess_workers,
                override_sequential=False,
            )
        )

        chunk_runs_bitrates = [command.best_bitrate for command in commands]

        avg_best = int(sum(chunk_runs_bitrates) / len(chunk_runs_bitrates))

        print(f"Best avg bitrate: {avg_best} kbps")

        if self.config.crf_bitrate_mode:
            print(f"Using capped crf mode, finding crf that matches the target bitrate")
            target_crf = self.get_target_crf(avg_best)
            print(f"Avg crf for {avg_best}Kpbs: {target_crf}")
            self.config.prototype_encoder.crf = target_crf
            self.config.max_bitrate = int(avg_best * 1.6)

        try:
            print("Saving bitrate ladder detection cache file")
            pickle.dump(avg_best, open(probe_folder + "cache.pt", "wb"))
        except:
            print("Failed to save cache file for best average bitrate")

        return avg_best

    def get_best_bitrate(self, skip_cache=False) -> int:
        """
        Doing a binary search on chunks, to find a bitrate that, on average, will yield config.vmaf
        :return: bitrate in kbps e.g., 2420
        """
        print("Finding best bitrate")
        probe_folder = f"{self.config.temp_folder}/adapt/bitrate/"

        if not skip_cache:
            if os.path.exists(probe_folder + "cache.pt"):
                try:
                    print("Found cache file, reading")
                    avg_best = pickle.load(open(probe_folder + "cache.pt", "rb"))
                    print(f"Best avg bitrate: {avg_best} kbps")
                    return avg_best
                except:
                    pass

        shutil.rmtree(probe_folder, ignore_errors=True)
        os.makedirs(probe_folder)

        print(
            f'Probing chunks: {" ".join([str(chunk.chunk_index) for chunk in self.chunks])}'
        )

        chunks = copy.deepcopy(self.chunks)

        encoder_extension = self.config.get_encoder().get_chunk_file_extension()

        # add proper paths
        for i, chunk in enumerate(chunks):
            chunk.chunk_index = i
            chunk.chunk_path = f"{probe_folder}{i}{encoder_extension}"

        commands = [GetBestBitrate(self, chunk) for chunk in chunks]

        loop = asyncio.get_event_loop()
        loop.run_until_complete(
            execute_commands(
                self.config.use_celery,
                commands,
                self.config.multiprocess_workers,
                override_sequential=False,
            )
        )

        chunk_runs_bitrates = [command.best_bitrate for command in commands]

        avg_best = int(sum(chunk_runs_bitrates) / len(chunk_runs_bitrates))

        print(f"Best avg bitrate: {avg_best} kbps")

        if self.config.crf_bitrate_mode:
            print(
                f"Using crf bitrate mode, finding crf that matches the target bitrate"
            )
            target_crf = self.get_target_crf(avg_best)
            print(f"Avg crf for {avg_best}Kpbs: {target_crf}")
            self.config.prototype_encoder.crf = target_crf
            self.config.max_bitrate = int(avg_best * 1.6)

        try:
            print("Saving bitrate ladder detection cache file")
            pickle.dump(avg_best, open(probe_folder + "cache.pt", "wb"))
        except:
            print("Failed to save cache file for best average bitrate")

        return avg_best

    def best_bitrate_single(self, chunk: ChunkObject) -> int:
        """
        :param chunk: chunk that we will be testing
        :return: ideal bitrate for that chunk based on self.config's vmaf
        """
        enc = self.config.get_encoder()
        enc.chunk = chunk
        enc.speed = 6
        enc.passes = 3
        enc.grain_synth = self.config.prototype_encoder.grain_synth
        enc.rate_distribution = EncoderRateDistribution.VBR
        enc.threads = 1
        enc.svt_bias_pct = 90

        runs = []

        left = 0
        right = self.max_bitrate
        num_probes = 0

        while left <= right and num_probes < self.num_probes:
            num_probes += 1
            mid_bitrate = (left + right) // 2
            enc.bitrate = mid_bitrate
            mid_vmaf = enc.run(
                timeout_value=300,
                calculate_vmaf=True,
                vmaf_params=VmafOptions(uhd=True, neg=True),
            ).vmaf_result.mean

            tqdm.write(f"{chunk.log_prefix()}{mid_bitrate} kbps -> {mid_vmaf} vmaf")

            runs.append((mid_bitrate, mid_vmaf))

            if mid_vmaf < self.config.vmaf:
                left = mid_bitrate + 1
            else:
                right = mid_bitrate - 1

        best_inter = min(runs, key=lambda x: abs(x[1] - self.config.vmaf))[0]

        tqdm.write(
            f"{chunk.log_prefix()}best interpolated bitrate {best_inter} kbps",
        )
        return int(best_inter)

    def best_crf_single(self, chunk: ChunkObject) -> int:
        """
        :param chunk: chunk that we will be testing
        :return: ideal crf for that chunk based on self.config's vmaf
        """
        encoder = self.config.get_encoder()
        encoder.chunk = chunk
        encoder.speed = 6
        encoder.passes = 1
        encoder.rate_distribution = EncoderRateDistribution.CQ
        encoder.threads = 1

        runs = []

        left = 0
        right = self.max_crf
        num_probes = 0

        while left <= right and num_probes < self.num_probes:
            num_probes += 1
            mid_crf = (left + right) // 2
            encoder.crf = mid_crf
            encoder.run(timeout_value=300)

            mid_vmaf = calculate_metric(
                chunk=chunk,
                video_filters=self.config.prototype_encoder.video_filters,
                vmaf_options=VmafOptions(
                    uhd=True,
                    neg=True,
                ),
            ).mean

            tqdm.write(f"{chunk.log_prefix()}{mid_crf} crf -> {mid_vmaf} vmaf")

            runs.append((mid_crf, mid_vmaf))

            if mid_vmaf < self.config.vmaf:
                right = mid_crf - 1
            else:
                left = mid_crf + 1

        best_inter = min(runs, key=lambda x: abs(x[1] - self.config.vmaf))[0]

        tqdm.write(
            f"{chunk.log_prefix()}best interpolated crf {best_inter} crf",
        )
        return int(best_inter)

    def remove_ssim_translate_cache(self):
        """
        Removes the ssim translate cache
        """
        shutil.rmtree(f"{self.config.temp_folder}/adapt/bitrate/ssim_translate")

    def get_target_ssimdb(self, bitrate: int) -> float:
        """
        Since in the AutoBitrate we are targeting ssim dB values, we need to somehow translate vmaf to ssim dB
        :param bitrate: bitrate in kbps
        :return: target ssimdb
        """
        print(f"Getting target ssim dB for {bitrate} kbps")
        cache_path = (
            f"{self.config.temp_folder}/adapt/bitrate/ssim_translate/{bitrate}.pl"
        )
        if os.path.exists(cache_path):
            try:
                target_ssimdb = pickle.load(open(cache_path, "rb"))
                print(f"cached ssim dB for {bitrate}: {target_ssimdb}dB")
                return target_ssimdb
            except:
                pass
        dbs = []
        os.makedirs(
            f"{self.config.temp_folder}/adapt/bitrate/ssim_translate", exist_ok=True
        )

        with ThreadPoolExecutor(max_workers=self.simultaneous_probes) as executor:
            for chunk in self.chunks:
                executor.submit(self.calulcate_ssimdb, bitrate, chunk, dbs)
            executor.shutdown()

        target_ssimdb = sum(dbs) / len(dbs)

        print(f"Avg ssim dB for {bitrate}Kbps: {target_ssimdb}dB")
        pickle.dump(target_ssimdb, open(cache_path, "wb"))
        return target_ssimdb

    def calulcate_ssimdb(self, bitrate: int, chunk: ChunkObject, dbs: List[float]):
        """
        Calculates the ssim dB for a chunk and appends it to the dbs list
        :param bitrate: bitrate in kbps
        :param chunk: chunk to calculate ssim dB for
        :param dbs: The list to append the ssim dB to
        """
        enc = self.config.get_encoder()
        enc.chunk = chunk
        enc.speed = 6
        enc.passes = 3
        enc.grain_synth = 0
        enc.rate_distribution = EncoderRateDistribution.VBR
        enc.threads = 1
        enc.bitrate = bitrate
        enc.output_path = (
            f"{self.config.temp_folder}adapt/bitrate/ssim_translate/{chunk.chunk_index}"
            f"{enc.get_chunk_file_extension()}"
        )
        enc.svt_bias_pct = 90
        try:
            stats: EncodeStats = enc.run(calcualte_ssim=True)
        except Exception as e:
            print(f"Failed to calculate ssim dB for {chunk.chunk_index}: {e}")
            return
        self.config.log(
            f"[{chunk.chunk_index}] {bitrate} kbps -> {stats.ssim_db} ssimdb"
        )
        dbs.append(stats.ssim_db)

    def crf_to_bitrate(self, crf: int, chunks: List[ChunkObject]) -> int:
        bitrates = []

        def sub(c: ChunkObject):
            encoder = self.config.get_encoder()
            encoder.chunk = c
            probe_folder = f"{self.config.temp_folder}/adapt/crf_to_bitrate/"
            os.makedirs(probe_folder, exist_ok=True)
            encoder.speed = 5
            encoder.passes = 1
            encoder.grain_synth = self.config.prototype_encoder.grain_synth
            encoder.rate_distribution = EncoderRateDistribution.CQ
            encoder.threads = 1
            encoder.crf = crf
            encoder.output_path = f"{probe_folder}{c.chunk_index}_{crf}{encoder.get_chunk_file_extension()}"

            stats = encoder.run(timeout_value=500)

            print(f"[{c.chunk_index}] {crf} crf -> {stats.bitrate} kb/s")
            bitrates.append(stats.bitrate)

        with ThreadPoolExecutor(max_workers=self.simultaneous_probes) as executor:
            for chunk in chunks:
                executor.submit(sub, chunk)
            executor.shutdown()

        final = int(sum(bitrates) / len(bitrates))

        print(f"on avreage crf {crf} -> {final} kb/s")
        return final

    def get_target_crf(self, bitrate: int) -> int:
        """
        Translate a bitrate roughly to a crf value
        :param bitrate: bitrate in kbps
        :return: the predicted crf
        """
        crfs = []

        def sub(c: ChunkObject):
            encoder = self.config.get_encoder()
            encoder.chunk = c
            encoder.speed = 5
            encoder.passes = 1
            encoder.grain_synth = self.config.prototype_encoder.grain_synth
            encoder.rate_distribution = EncoderRateDistribution.CQ
            encoder.threads = 1

            probe_folder = f"{self.config.temp_folder}/adapt/bitrate/"
            os.makedirs(probe_folder, exist_ok=True)

            max_probes = 4
            left = 0
            right = 40
            num_probes = 0

            runs = []

            while left <= right and num_probes < max_probes:
                num_probes += 1
                mid = (left + right) // 2
                encoder.crf = mid
                encoder.output_path = f"{probe_folder}{c.chunk_index}_{mid}{encoder.get_chunk_file_extension()}"
                stats = encoder.run(timeout_value=500)

                print(f"[{c.chunk_index}] {mid} crf ~> {stats.bitrate} kb/s")

                runs.append((mid, stats.bitrate))

                if stats.bitrate > bitrate:
                    left = mid + 1
                else:
                    right = mid - 1

            # find two points that are closest to the target bitrate
            point1 = min(runs, key=lambda x: abs(x[1] - bitrate))
            runs.remove(point1)
            point2 = min(runs, key=lambda x: abs(x[1] - bitrate))

            # linear interpolation to find the bitrate that gives us the target bitrate
            best_inter = point1[0] + (point2[0] - point1[0]) * (bitrate - point1[1]) / (
                point2[1] - point1[1]
            )
            best_inter = int(best_inter)
            print(
                f"[{c.chunk_index}] INTERPOLATED: {best_inter} crf ~> {bitrate} bitrate"
            )
            crfs.append(best_inter)

        with ThreadPoolExecutor(max_workers=self.simultaneous_probes) as executor:
            for chunk in self.chunks:
                executor.submit(sub, chunk)
            executor.shutdown()

        final = int(sum(crfs) / len(crfs))

        print(f"Average crf for {bitrate} -> {final}")
        return final


class GetBestBitrate(BaseCommandObject):
    """
    Wrapper around AutoBitrateLadder.get_best_bitrate to execute on our framework
    """

    def __init__(self, auto_bitrate_ladder: AutoBitrateLadder, chunk: ChunkObject):
        self.best_bitrate = None
        self.auto_bitrate_ladder = auto_bitrate_ladder
        self.chunk = chunk

    def run(self):
        self.best_bitrate = self.auto_bitrate_ladder.best_bitrate_single(self.chunk)


class GetBestCrf(BaseCommandObject):
    def __init__(self, auto_bitrate_ladder: AutoBitrateLadder, chunk: ChunkObject):
        self.best_crf = None
        self.autobitrate_ladder = auto_bitrate_ladder
        self.chunk = chunk

    def run(self):
        self.best_crf = self.autobitrate_ladder.best_crf_single(self.chunk)


class GetComplexity(BaseCommandObject):
    def __init__(self, auto_bitrate_ladder: AutoBitrateLadder, chunk: ChunkObject):
        self.complexity = None
        self.auto_bitrate_ladder = auto_bitrate_ladder
        self.chunk = chunk

    def run(self):
        self.complexity = self.auto_bitrate_ladder.get_complexity(
            c=self.chunk, enc=self.auto_bitrate_ladder.config.get_encoder()
        )