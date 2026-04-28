"""Tool-specific compressors."""

from snipp.compressors.base import CompressResult, BaseCompressor
from snipp.compressors.grep import GrepCompressor
from snipp.compressors.git import GitLogCompressor, GitDiffCompressor, GitStatusCompressor
from snipp.compressors.cat import CatCompressor
from snipp.compressors.ls import LsCompressor, FindCompressor
from snipp.compressors.pytest import PytestCompressor
from snipp.compressors.generic import GenericCompressor
from snipp.compressors.docker import DockerCompressor
from snipp.compressors.npm_test import NpmTestCompressor
from snipp.compressors.npm import NpmCompressor
from snipp.compressors.eslint import EslintCompressor

__all__ = [
    "CompressResult",
    "BaseCompressor",
    "GrepCompressor",
    "GitLogCompressor",
    "GitDiffCompressor",
    "GitStatusCompressor",
    "CatCompressor",
    "LsCompressor",
    "FindCompressor",
    "PytestCompressor",
    "GenericCompressor",
    "DockerCompressor",
    "NpmTestCompressor",
    "NpmCompressor",
    "EslintCompressor",
]
