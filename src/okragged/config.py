from __future__ import annotations

import tomllib
from pathlib import Path

from pydantic import BaseModel, Field


class BundleConfig(BaseModel):
    root: str = "bundle"
    okf_version: str = "0.2"
    actor: str = "okragged/0.1.0"
    title: str | None = None
    """Heading of the root index.md. None -> the project directory name, which
    names the tool rather than the knowledge unless the two happen to agree."""


class LocalSourceConfig(BaseModel):
    family: str = "document"
    path: str = "sources"
    include: list[str] = Field(default_factory=lambda: ["**/*.md"])
    exclude: list[str] = Field(default_factory=list)


class SynthConfig(BaseModel):
    provider: str = "anthropic"
    model: str | None = None
    """None -> the provider's DEFAULT_MODEL."""
    effort: str = "high"
    """Anthropic only. Gemini has no equivalent; thinking stays at its default."""
    max_tokens: int = 16000


class TypesConfig(BaseModel):
    allowed: list[str] = Field(default_factory=list)
    allow_new: bool = True


class VectorsConfig(BaseModel):
    provider: str = "gemini"
    """Not DESIGN §12 Risk 4's answer — that is measured on a real corpus. This is
    the one that needs no extra credential and no extra dependency."""
    model: str | None = None
    """None -> the provider's DEFAULT_MODEL."""
    dim: int = 768
    """Requested output width. Providers with a fixed width ignore it."""
    knn_k: int = 8
    ghost_threshold: float = 0.82


class ProjectionConfig(BaseModel):
    method: str = "umap"
    """umap | pca. UMAP falls back to PCA below 15 concepts or without umap-learn."""
    random_state: int = 42


class Config(BaseModel):
    project_root: Path
    bundle: BundleConfig = Field(default_factory=BundleConfig)
    local: LocalSourceConfig = Field(default_factory=LocalSourceConfig)
    synth: SynthConfig = Field(default_factory=SynthConfig)
    types: TypesConfig = Field(default_factory=TypesConfig)
    vectors: VectorsConfig = Field(default_factory=VectorsConfig)
    projection: ProjectionConfig = Field(default_factory=ProjectionConfig)

    @property
    def bundle_root(self) -> Path:
        return self.project_root / self.bundle.root

    @property
    def sources_root(self) -> Path:
        return self.project_root / self.local.path

    @property
    def sidecar_root(self) -> Path:
        return self.project_root / ".okragged"

    @property
    def bundle_title(self) -> str:
        if self.bundle.title:
            return self.bundle.title
        return self.project_root.name.replace("-", " ").replace("_", " ").title()


def load(project_root: Path | None = None) -> Config:
    root = (project_root or Path.cwd()).resolve()
    path = root / "okragged.toml"
    # utf-8-sig: hand-edited file, so tolerate a BOM (tomllib would reject it)
    raw = tomllib.loads(path.read_text(encoding="utf-8-sig")) if path.exists() else {}
    return Config(
        project_root=root,
        bundle=BundleConfig(**raw.get("bundle", {})),
        local=LocalSourceConfig(**raw.get("sources", {}).get("local", {})),
        synth=SynthConfig(**raw.get("synth", {})),
        types=TypesConfig(**raw.get("types", {})),
        vectors=VectorsConfig(**raw.get("vectors", {})),
        projection=ProjectionConfig(**raw.get("projection", {})),
    )
