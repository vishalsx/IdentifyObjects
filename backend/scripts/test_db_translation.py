import asyncio
import os
import sys
from dotenv import load_dotenv

# Add backend directory to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from services.update_embeddings import translate_text, get_language_code

async def test():
    load_dotenv()
    print("--- Testing Database-driven Language Mapping ---")
    
    test_languages = ["Hindi", "Spanish", "French", "Portuguese", "UnknownLanguage"]
    
    for lang in test_languages:
        code = await get_language_code(lang)
        print(f"Language: {lang} -> ISO Code: {code}")
    
    print("\n--- Testing Full Translation Flow ---")
    # Test Portuguese specifically since user provided the example
    res = await translate_text("How are you?", "Portuguese")
    print(f"Text: 'How are you?' to Portuguese -> {res}")

if __name__ == "__main__":
    asyncio.run(test())
