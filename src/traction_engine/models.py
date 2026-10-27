from typing import Literal

from pydantic import BaseModel, Field, model_validator

Seniority = Literal["junior", "mid", "senior", "lead", "staff", "principal", "unstated"]
EmploymentType = Literal["full_time", "contract", "contract_to_hire", "part_time", "unstated"]
ContractTerms = Literal["w2", "1099", "c2c", "unstated"]
Source = Literal["direct", "agency", "aggregator", "unstated"]
Workplace = Literal["remote", "hybrid", "onsite", "unstated"]
CompPeriod = Literal["year", "hour", "unstated"]
Category = Literal[
    "language", "framework", "ai_ml", "cloud_infra", "data", "practice",
    "domain", "education", "soft_skill", "other",
]


class Requirement(BaseModel):
    name: str = Field(description="Short, normalized label, e.g. 'Python', 'RAG', 'Kubernetes'.")
    category: Category
    alternatives: list[str] = Field(
        description="For either/or requirements ('React or Vue'), each acceptable option. Empty otherwise."
    )
    quote: str = Field(description="The exact phrase from the posting this came from.")


class Requirements(BaseModel):
    title: str | None
    company: str | None = Field(description="The hiring company, not the staffing agency.")
    seniority: Seniority
    employment_type: EmploymentType
    contract_terms: ContractTerms
    source: Source
    intermediary: str | None = Field(description="Staffing agency, recruiter firm, or aggregator site, if any.")
    workplace: Workplace
    locations: list[str]
    years_min: int | None
    domain: str | None = Field(description="Industry, e.g. 'healthcare', 'legal', 'insurance'.")
    comp_min: float | None
    comp_max: float | None
    comp_period: CompPeriod
    must_have: list[Requirement]
    nice_to_have: list[Requirement]

    @model_validator(mode="after")
    def fields_consistent(self):
        if self.comp_min is not None and self.comp_max is not None and self.comp_min > self.comp_max:
            raise ValueError(f"comp_min {self.comp_min} is greater than comp_max {self.comp_max}")
        if self.intermediary and self.source not in ("agency", "aggregator"):
            raise ValueError(
                f"intermediary is {self.intermediary!r} but source is {self.source!r}; "
                "use 'agency' or 'aggregator', or set intermediary to null"
            )
        return self
