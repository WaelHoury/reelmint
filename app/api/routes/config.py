"""Configuration API routes (styles, settings)."""

from fastapi import APIRouter
from pydantic import BaseModel
from typing import List

router = APIRouter(tags=["config"])


class StyleInfo(BaseModel):
    id: str
    name: str
    description: str


class StylesResponse(BaseModel):
    styles: List[StyleInfo]


@router.get("/styles", response_model=StylesResponse)
async def get_narration_styles():
    """Get available narration styles."""
    from app.core import ScenePlanner

    styles = []
    for style_id, style_data in ScenePlanner.NARRATION_STYLES.items():
        styles.append(StyleInfo(
            id=style_id,
            name=style_id.capitalize(),
            description=style_data['voice'],
        ))

    return StylesResponse(styles=styles)
