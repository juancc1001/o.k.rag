from __future__ import annotations

import tomllib
from pathlib import Path

from pydantic import BaseModel, Field


class BundleConfig(BaseModel):
    root: str = "bundle"
    okf_version: str = "0.2"
    actor: str = "ragged/0.1.0"


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


class Config(BaseModel):
    project_root: Path
    bundle: BundleConfig = Field(default_factory=BundleConfig)
    local: LocalSourceConfig = Field(default_factory=LocalSourceConfig)
    synth: SynthConfig = Field(default_factory=SynthConfig)
    types: TypesConfig = Field(default_factory=TypesConfig)

    @property
    def bundle_root(self) -> Path:
        return self.project_root / self.bundle.root

    @property
    def sources_root(self) -> Path:
        return self.project_root / self.local.path

    @property
    def sidecar_root(self) -> Path:
        return self.project_root / ".ragged"


def load(project_root: Path | None = None) -> Config:
    root = (project_root or Path.cwd()).resolve()
    path = root / "ragged.toml"
    raw = tomllib.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    return Config(
        project_root=root,
        bundle=BundleConfig(**raw.get("bundle", {})),
        local=LocalSourceConfig(**raw.get("sources", {}).get("local", {})),
        synth=SynthConfig(**raw.get("synth", {})),
        types=TypesConfig(**raw.get("types", {})),
    )
