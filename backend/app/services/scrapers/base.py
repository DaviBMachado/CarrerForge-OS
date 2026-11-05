from dataclasses import dataclass


@dataclass
class ScrapedJob:
    platform: str
    external_id: str | None
    title: str
    company: str
    location: str | None = None
    employment_type: str | None = None
    remote_type: str | None = None
    requirements: str | None = None
    description: str = ""
    url: str | None = None
    language: str = "pt"
