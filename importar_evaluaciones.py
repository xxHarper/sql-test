"""Validate or import local examination files without touching candidate answers."""
import argparse
from pathlib import Path
from sql_assessment import create_app
from sql_assessment.exams import ExamValidationError, load_documents


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directorio', type=Path, default=Path(__file__).resolve().parent / 'evaluaciones')
    parser.add_argument('--validar', action='store_true', help='Validar sin abrir ni modificar SQLite')
    args = parser.parse_args()
    try:
        if args.validar:
            documents = load_documents(args.directorio)
            for filename, exam in documents:
                print(f'Válido: {filename} · {exam["exam_id"]} · {len(exam["questions"])} preguntas · 100 puntos')
        else:
            app = create_app({'EXAMS_DIR':str(args.directorio.resolve())})
            for result in app.extensions['exam_imports']:
                print(f'{result["exam_id"]} · versión {result["version"]} · {"nueva" if result["created"] else "ya existente, sin duplicar"}')
    except ExamValidationError as error:
        raise SystemExit(str(error))


if __name__ == '__main__':
    main()
