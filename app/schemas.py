"""Pydantic DTOs returned by the JSON API."""

from datetime import datetime

from pydantic import BaseModel, Field


class UploadResponse(BaseModel):
    filename: str = Field(description="실제 저장된 파일명", examples=["performance-report.html"])
    url: str = Field(
        description="브라우저에서 바로 열 수 있는 보고서의 절대 URL",
        examples=["https://example.com/docs/performance-report"],
        json_schema_extra={"format": "uri"},
    )
    uploaded_at: datetime = Field(
        description="업로드 완료 시각 (ISO 8601, 설정된 시간대 기준)",
        examples=["2026-10-27T23:00:00+09:00"],
    )


class ReportItem(BaseModel):
    name: str = Field(description="보고서 이름 (.html 제외, /docs/{name} 에 사용)", examples=["performance-report"])
    filename: str = Field(description="저장된 파일명", examples=["performance-report.html"])
    url: str = Field(
        description="브라우저에서 바로 열 수 있는 보고서의 절대 URL",
        examples=["https://example.com/docs/performance-report"],
        json_schema_extra={"format": "uri"},
    )
    size: int = Field(description="파일 크기 (바이트)", examples=[48213])
    uploaded_at: datetime = Field(
        description="업로드 날짜 (파일 최종 수정 시각, ISO 8601)",
        examples=["2026-10-27T23:00:00+09:00"],
    )


class ReportListResponse(BaseModel):
    count: int = Field(description="보고서 개수", examples=[1])
    reports: list[ReportItem] = Field(description="보고서 목록 (최신순)")


class ErrorResponse(BaseModel):
    detail: str = Field(description="오류 설명", examples=["Report 'performance-report.html' already exists."])
