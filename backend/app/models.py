from typing import Literal
from pydantic import BaseModel, Field, ConfigDict, model_validator

class Combination(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False, extra='forbid')
    name: str = Field(min_length=1, max_length=60)
    leg_a: str = Field(pattern=r'^[A-Z]{1,3}\d{4}$')
    leg_b: str = Field(pattern=r'^[A-Z]{1,3}\d{4}$')
    mode: Literal['spread', 'ratio', 'weighted'] = 'spread'
    coefficient_a: float = Field(default=1, gt=0, le=100)
    coefficient_b: float = Field(default=1, gt=0, le=100)
    favorite: bool = False
    @model_validator(mode='after')
    def normalize(self):
        self.name = self.name.strip()
        if not self.name:
            raise ValueError('组合名称不能为空')
        if self.mode != 'weighted':
            self.coefficient_a = self.coefficient_b = 1
        return self

class AnalysisRequest(BaseModel):
    combination: Combination
    period: Literal['1m','5m','15m','30m','60m','2h','4h','1d'] = '1d'
    count: int = Field(default=300, ge=130, le=600)

class Reorder(BaseModel):
    ids: list[str]

class Alert(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False, extra='forbid')
    combination_id: str
    metric: Literal['price','percentile','zscore'] = 'price'
    operator: Literal['gte','lte'] = 'gte'
    threshold: float
    enabled: bool = True
    @model_validator(mode='after')
    def check_threshold(self):
        if self.metric == 'percentile' and not 0 <= self.threshold <= 100:
            raise ValueError('分位数阈值必须在 0–100 之间')
        return self
