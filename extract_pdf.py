import fitz  # PyMuPDF
import sys
import os


def extract_pdf_to_text(pdf_path, output_path=None):
    """Extract text from a PDF file and save it to a text file."""
    if not os.path.exists(pdf_path):
        print(f"Error: File '{pdf_path}' not found.")
        sys.exit(1)

    if output_path is None:
        base_name = os.path.splitext(pdf_path)[0]
        output_path = base_name + "_extracted.txt"

    try:
        doc = fitz.open(pdf_path)
        full_text = []

        print(f"Processing '{pdf_path}' ({len(doc)} pages)...")

        for page_num in range(len(doc)):
            page = doc[page_num]
            text = page.get_text("text")
            if text.strip():
                full_text.append(f"--- PAGE {page_num + 1} ---\n")
                full_text.append(text)
                full_text.append("\n")

        doc.close()

        combined_text = "\n".join(full_text)

        with open(output_path, "w", encoding="utf-8") as f:
            f.write(combined_text)

        print(f"Extracted text saved to '{output_path}'")
        print(f"Total characters: {len(combined_text)}")
        return combined_text

    except Exception as e:
        print(f"Error processing PDF: {e}")
        sys.exit(1)


if __name__ == "__main__":
    pdf_file = "AWS Certified Solutions Architect Associate SAA-C03.pdf"
    extract_pdf_to_text(pdf_file)
