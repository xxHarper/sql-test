"""Test expectations derive from the official document, never a second question bank."""
import json
from pathlib import Path
DOCUMENT = json.loads((Path(__file__).resolve().parents[1] / 'evaluaciones/reactivos_sql_junior.json').read_text(encoding='utf-8'))
QUESTIONS = [dict(id=i, points=q['points'], kind={'multiple_choice':'choice','sql_text':'sql','open_text':'text'}[q['type']], correct=q['grading'].get('correct_option')) for i,q in enumerate(DOCUMENT['questions'],1)]
