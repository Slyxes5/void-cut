from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator

Platform = Literal['youtube', 'tiktok']


class UploadTarget(BaseModel):
    model_config = ConfigDict(extra='forbid')
    platform: Platform
    account_id: str = Field(min_length=1, max_length=256)
    mode: Literal['upload', 'draft', 'direct']
    privacy: str = Field(default='', max_length=40)
    made_for_kids: bool = False
    contains_synthetic_media: bool = False
    allow_comment: bool = False
    allow_duet: bool = False
    allow_stitch: bool = False
    commercial: bool = False
    own_brand: bool = False
    branded_content: bool = False
    music_consent: bool = False

    @model_validator(mode='after')
    def valid_mode(self):
        if self.platform == 'youtube':
            if self.mode != 'upload' or self.privacy not in {'private', 'unlisted', 'public'}:
                raise ValueError('Pilih visibilitas upload YouTube.')
        elif self.mode not in {'draft', 'direct'}:
            raise ValueError('Mode TikTok tidak valid.')
        elif self.mode == 'direct':
            if not self.privacy or not self.music_consent:
                raise ValueError('Pilih privasi TikTok dan setujui penggunaan musik.')
            if self.commercial and not (self.own_brand or self.branded_content):
                raise ValueError('Pilih jenis konten komersial.')
            if self.branded_content and self.privacy not in {'PUBLIC_TO_EVERYONE','MUTUAL_FOLLOW_FRIENDS'}:
                raise ValueError('Branded content memerlukan privasi Everyone atau Friends.')
        return self


class SocialUploadRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    request_id: str = Field(pattern=r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')
    batch_id: str = Field(pattern=r'^batch_[0-9]+_[0-9a-f]{6}$')
    clip_index: int = Field(ge=0, strict=True)
    title: str = Field(min_length=1, max_length=100)
    caption: str = Field(default='', max_length=5000)
    targets: list[UploadTarget] = Field(min_length=1, max_length=2)
    confirmed: Literal[True]

    @model_validator(mode='after')
    def valid_metadata(self):
        if not self.title.strip() or '<' in self.title or '>' in self.title:
            raise ValueError('Judul wajib diisi dan tidak boleh berisi < atau >.')
        if len({target.platform for target in self.targets}) != len(self.targets):
            raise ValueError('Setiap platform hanya boleh dipilih satu kali.')
        description = '\n\n'.join(x for x in (self.title.strip(), self.caption.strip()) if x)
        if any(t.platform == 'tiktok' and t.mode == 'direct' for t in self.targets):
            if len(description.encode('utf-16-le')) // 2 > 2200:
                raise ValueError('Judul + caption TikTok maksimal 2200 karakter UTF-16.')
        if len(self.caption.encode('utf-8')) > 5000:
            raise ValueError('Deskripsi YouTube maksimal 5000 byte.')
        return self
