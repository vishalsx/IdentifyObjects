from typing import List, Optional
from pydantic import BaseModel, Field, GetCoreSchemaHandler
from bson import ObjectId
from datetime import datetime,timezone
from pydantic_core import core_schema
from services.userauth import get_current_user_id


# ---------- Custom ObjectId ----------
class PyObjectId(ObjectId):
    """Custom ObjectId type compatible with Pydantic v2."""

    @classmethod
    def __get_pydantic_core_schema__(cls, source_type, handler: GetCoreSchemaHandler):
        return core_schema.no_info_after_validator_function(
            cls.validate,
            core_schema.str_schema()
        )

    @classmethod
    def validate(cls, v, info=None):
        if not v:
            raise ValueError("ObjectId cannot be empty")
        if not ObjectId.is_valid(v):
            raise ValueError(f"Invalid ObjectId: {v}")
        return ObjectId(v)

    @classmethod
    def __get_pydantic_json_schema__(cls, core_schema, handler):
        json_schema = handler(core_schema)
        json_schema.update(type="string", example="60b8d295f8d2c8f9f4e4b8d3")
        return json_schema


class ImageRef(BaseModel):
    image_id: Optional[str] = Field(None, description="Unique identifier for the image (DB or local)")
    image_hash: str = Field(..., description="Hash of the image for integrity verification")
    position: Optional[int] = Field(None, description="Order of image within the page")
    object_name: Optional[str] = Field(None, description="Name of the object in the image")
    model_config = {"json_encoders": {ObjectId: str}}


class Page(BaseModel):
    page_id: Optional[str] = Field(None, description="Unique identifier for the page (DB or local)")
    page_number: Optional[int] = Field(None, ge=1, description="Page number within the chapter")
    title: Optional[str] = None
    images: List[ImageRef] = Field(default_factory=list)
    story: Optional[str] = None
    moral: Optional[str] = None
    model_config = {"json_encoders": {ObjectId: str}}


class Chapter(BaseModel):
    chapter_id: Optional[str] = Field(None, description="Unique identifier for the chapter (DB or local)")
    chapter_number: Optional[int] = Field(None, ge=1, description="Sequential number of the chapter")
    chapter_name: str
    description: Optional[str] = None
    pages: List[Page] = Field(default_factory=list)

    model_config = {"json_encoders": {ObjectId: str}}


# ---------- Book Model ----------
class Book(BaseModel):
    id: Optional[PyObjectId] = Field(default_factory=PyObjectId, alias="_id")
    title: str
    language: str
    author: Optional[str] = None
    subject: Optional[str] = None
    education_board: Optional[str] = None
    grade_level: Optional[str] = None
    tags: Optional[List[str]] = Field(default_factory=list)
    chapters: Optional[List[Chapter]] = Field(default_factory=list)

    # --- New Count Fields ---
    chapter_count: int = Field(0, description="Total number of chapters in the book")
    page_count: int = Field(0, description="Total number of pages across all chapters")
    image_count: int = Field(0, description="Total number of images across all pages")
    book_status: str = Field("Draft", description="Status of the book: Draft/Released/Verified/Approved")
    # created_by: Optional[str] = Field(None, description="User ID of the creator")
    created_by: Optional[str] = Field(get_current_user_id(), description="User ID of the creator")
    created_at: Optional[datetime] = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: Optional[datetime] = Field(default_factory=lambda: datetime.now(timezone.utc))


    model_config = {
        "arbitrary_types_allowed": True,
        "json_encoders": {ObjectId: str}
    }



# # ---------- Image Model ----------
# class ImageRef(BaseModel):
#     image_id: str = Field(..., description="Unique identifier for the image")
#     # translation_id: Optional[PyObjectId] = Field(None, description="Reference to translation document (_id)")
#     # object_id: Optional[PyObjectId] = Field(None, description="Reference to the object in 'objects' collection")
#     image_hash: str = Field(..., description="Hash of the image for integrity verification")
#     position: Optional[int] = Field(None, description="Order of image within the page")

#     model_config = {
#         "arbitrary_types_allowed": True,
#         "json_encoders": {ObjectId: str}
#     }


# # ---------- Page Model ----------
# class Page(BaseModel):
#     page_id: str = Field(..., description="Unique identifier for the page")
#     page_number: Optional[int] = Field(..., ge=1)
#     title: Optional[str] = None
#     images: Optional[List[ImageRef]] = Field(default_factory=list)

#     model_config = {
#         "arbitrary_types_allowed": True,
#         "json_encoders": {ObjectId: str}
#     }


# # ---------- Chapter Model ----------
# class Chapter(BaseModel):
#     chapter_id: str = Field(..., description="Unique identifier for the chapter")
#     chapter_number: int = Field(..., ge=1)
#     chapter_name: str
#     description: Optional[str] = None
#     pages: Optional[List[Page]] = Field(default_factory=list)

#     model_config = {
#         "arbitrary_types_allowed": True,
#         "json_encoders": {ObjectId: str}
#     }


# ---------- STORY MODELS ----------
class PageStoryRequest(BaseModel):
    book_id: str
    chapter_id: Optional[str] = None
    page_id: Optional[str] = None
    user_comments: Optional[str] = None


class StoryResponse(BaseModel):
    book_id: str
    chapter_id: str
    page_id: str
    language: str
    object_names: List[str]
    story: str
    moral: Optional[str]
    created_at: datetime

