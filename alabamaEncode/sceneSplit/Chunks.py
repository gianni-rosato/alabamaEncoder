from typing import List

from alabamaEncode.sceneSplit.ChunkOffset import ChunkObject


class ChunkSequence:
    """
    A sequence of chunks.
    """

    def __init__(self, chunks: List[ChunkObject]):
        self.chunks = chunks
        self.input_file = ""

    def get_specific_chunk(self, index: int) -> ChunkObject:
        return self.chunks[index]

    def __len__(self):
        return len(self.chunks)