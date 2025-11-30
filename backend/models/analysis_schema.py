from pydantic import BaseModel, Field

# Define the structured output schema
class AnalysisResult(BaseModel):
    """Structured analysis of an image, including translations."""
    object_name_en: str = Field(description="The exact name of the object in English (e.g., 'Rose', 'Eiffel Tower').")
    object_name: str = Field(description="The object name in the target_language using the provided language_script (e.g., in Hindi 'सेब'). Must be a noun without classifiers.")
    translated_to: str = Field(description="The name of the target_language (e.g., 'Hindi', 'Spanish').")
    object_description: str = Field(description="A detailed description (25–75 words) in the target_language and language_script, covering origin, usage, properties, and a regional trivia/fun fact.")
    object_hint: str = Field(description="A riddle, proverb, or cultural saying in the target_language and language_script, designed for a guessing game, without revealing the object name.")
    object_short_hint: str = Field(description="A very brief hint (10–15 words max) in the target_language and language_script.")
    tags: list[str] = Field(description="A list of 5-8 relevant, lowercase tags in English (e.g., ['food', 'fruit', 'red', 'healthy']).")
    object_category: str = Field(description="Category in English (e.g., 'plant', 'flower', 'animal', 'building', 'vehicle', 'food', 'clothing', 'tool', 'furniture', 'other').")
    field_of_study: str = Field(description="Field of study in English (e.g., 'botany', 'zoology', 'architecture', 'culinary arts').")
    age_appropriate: str = Field(description="Age appropriateness in English (Must be one of: 'all ages', 'kids', 'teens', 'adults', 'seniors').")
    error: str = Field(default="", description="If inappropriate content is detected, this field should be populated with 'Inappropriate content detected. Can't be processed.' All other fields must be empty strings/lists.")