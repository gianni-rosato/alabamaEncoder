from alabamaEncode.alabamaPath import PathAlabama
from alabamaEncode.ffmpeg import Ffmpeg


def get_height(in_path):
    # if not os.path.exists(in_path):
    #     raise FileNotFoundError(f"File {in_path} does not exist")
    # argv_ = f'ffprobe -v error -select_streams v:0 -show_entries stream=height -of csv=p=0 "{in_path}"'
    # result = syscmd(argv_)
    # result = result.strip()
    # result = result.replace(",", "")
    # return int(result)

    return Ffmpeg.get_height(PathAlabama(in_path))
