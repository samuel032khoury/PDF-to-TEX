#!/usr/bin/env python3
"""
PDF to LaTeX Converter Pipeline

This script converts PDF files to general Markdown format with extracted images,
then uses Pandoc to convert the Markdown files to LaTeX (.tex) files.
"""

import os
import json
import base64
import shutil
import subprocess
from pathlib import Path
from dotenv import load_dotenv
from mistralai import Mistral, DocumentURLChunk
from mistralai.models import OCRResponse

# Path configuration
INPUT_DIR = Path("pdfs_to_process")   # Folder where the user places the PDFs to be processed
DONE_DIR = Path("pdfs-done")          # Folder where processed PDFs will be moved
OUTPUT_ROOT_DIR = Path("ocr_output")  # Root folder for conversion results
LATEX_OUTPUT_DIR = Path("latex_output")  # Folder for LaTeX output

# Ensure directories exist
INPUT_DIR.mkdir(exist_ok=True)
DONE_DIR.mkdir(exist_ok=True)
OUTPUT_ROOT_DIR.mkdir(exist_ok=True)
LATEX_OUTPUT_DIR.mkdir(exist_ok=True)

# Load API Key
load_dotenv()
api_key = os.getenv("MISTRAL_API_KEY")
if not api_key:
    raise ValueError("MISTRAL_API_KEY environment variable is not set. Get a free API Key at: https://console.mistral.ai/api-keys")
print(f"Loaded API Key: {api_key[:4]}...")
client = Mistral(api_key=api_key)


def replace_images_in_markdown(markdown_str: str, images_dict: dict) -> str:
    """
    This converts base64 encoded images directly in the markdown...
    And replaces them with links to external images, so the markdown is more readable and organized.
    """
    for img_name, base64_str in images_dict.items():
        markdown_str = markdown_str.replace(f"![{img_name}]({img_name})", f"![{img_name}]({base64_str})")
    return markdown_str


def get_combined_markdown(ocr_response: OCRResponse) -> str:
    """
    Part of the response from the Mistral API, which is an OCRResponse object...
    And returns a single string with the combined markdown of all the pages of the PDF.
    """
    markdowns: list[str] = []
    for page in ocr_response.pages:
        image_data = {}
        for img in page.images:
            image_data[img.id] = img.image_base64
        markdowns.append(replace_images_in_markdown(page.markdown, image_data))

    return "\n\n".join(markdowns)


def process_pdf(pdf_path: Path):
    """
    Process a single PDF file and convert it to markdown
    """
    # PDF base name
    pdf_base = pdf_path.stem
    print(f"Processing {pdf_path.name} ...")
    
    # Output folders
    output_dir = OUTPUT_ROOT_DIR / pdf_base
    output_dir.mkdir(exist_ok=True)
    images_dir = output_dir / "images"
    images_dir.mkdir(exist_ok=True)
    
    # PDF -> OCR
    with open(pdf_path, "rb") as f:
        pdf_bytes = f.read()
        
    uploaded_file = client.files.upload(
        file={
            "file_name": pdf_path.name,
            "content": pdf_bytes,
        },
        purpose="ocr"
    )
    
    signed_url = client.files.get_signed_url(file_id=uploaded_file.id, expiry=1)
    
    ocr_response = client.ocr.process(
        document=DocumentURLChunk(document_url=signed_url.url),
        model="mistral-ocr-latest",
        include_image_base64=True
    )
    
    # Save OCR in JSON 
    # (in case something fails it could be reused, but it is not used in the rest of the code)
    ocr_json_path = output_dir / "ocr_response.json"
    with open(ocr_json_path, "w", encoding="utf-8") as json_file:
        json.dump(ocr_response.model_dump(), json_file, indent=4, ensure_ascii=False)
    print(f"OCR response saved in {ocr_json_path}")
    
    # OCR -> Standard Markdown
    # - From base64 encoded images, converts them to links to 
    #   external images and generates the images in a subfolder.
    
    global_counter = 1
    updated_markdown_pages = []
    
    for page in ocr_response.pages:
        updated_markdown = page.markdown
        for image_obj in page.images:
            
            # base64 to image
            base64_str = image_obj.image_base64
            if base64_str.startswith("data:"):
                base64_str = base64_str.split(",", 1)[1]
            image_bytes = base64.b64decode(base64_str)
            
            # image extensions
            ext = Path(image_obj.id).suffix if Path(image_obj.id).suffix else ".png"
            new_image_name = f"{pdf_base}_img_{global_counter}{ext}"
            global_counter += 1
            
            # save in subfolder
            image_output_path = images_dir / new_image_name
            with open(image_output_path, "wb") as f:
                f.write(image_bytes)
            
            # Update markdown with standard markdown image syntax: ![alt text](path/to/image)
            # Using relative path to make it compatible with general markdown
            updated_markdown = updated_markdown.replace(
                f"![{image_obj.id}]({image_obj.id})",
                f"![{image_obj.id}](images/{new_image_name})"
            )
        updated_markdown_pages.append(updated_markdown)
    
    final_markdown = "\n\n".join(updated_markdown_pages)
    output_markdown_path = output_dir / "output.md"
    with open(output_markdown_path, "w", encoding="utf-8") as md_file:
        md_file.write(final_markdown)
    print(f"Markdown generated in {output_markdown_path}")
    
    return output_markdown_path, images_dir


def convert_md_to_latex(markdown_path: Path, images_dir: Path):
    """
    Convert Markdown file to LaTeX using Pandoc
    """
    print(f"Converting {markdown_path} to LaTeX...")
    
    # Create output LaTeX directory with same name as the PDF
    pdf_name = markdown_path.parent.name
    latex_output_path = LATEX_OUTPUT_DIR / pdf_name
    latex_output_path.mkdir(exist_ok=True)
    
    # Create images directory in LaTeX output
    latex_images_dir = latex_output_path / "images"
    if not latex_images_dir.exists():
        # Copy images directory from markdown output
        shutil.copytree(images_dir, latex_images_dir)
    
    tex_file_path = latex_output_path / f"{pdf_name}.tex"
    
    # Pandoc command to convert MD to LaTeX
    # --standalone: produce a standalone document
    # --from=markdown: specify input format
    # --to=latex: specify output format
    # --output: specify output file
    pandoc_cmd = [
        "pandoc",
        str(markdown_path),
        "--standalone",
        "--from=markdown",
        "--to=latex",
        f"--output={tex_file_path}"
    ]
    
    try:
        # Run pandoc command
        result = subprocess.run(
            pandoc_cmd, 
            check=True, 
            stdout=subprocess.PIPE, 
            stderr=subprocess.PIPE,
            text=True
        )
        print(f"LaTeX file successfully generated: {tex_file_path}")
        return tex_file_path
    except subprocess.CalledProcessError as e:
        print(f"Error running pandoc: {e}")
        print(f"Command output: {e.stdout}")
        print(f"Command error: {e.stderr}")
        return None
    except FileNotFoundError:
        print("Error: Pandoc not found. Please make sure pandoc is installed and in your PATH.")
        print("Install instructions: https://pandoc.org/installing.html")
        return None


def main():
    """
    Main function to process all PDFs in the input directory
    """
    # Check if pandoc is installed
    try:
        subprocess.run(["pandoc", "--version"], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except (subprocess.SubprocessError, FileNotFoundError):
        print("Error: Pandoc is not installed or not in PATH.")
        print("Please install Pandoc: https://pandoc.org/installing.html")
        print("Continuing with PDF to Markdown conversion only...")
        pandoc_available = False
    else:
        pandoc_available = True
        print("Pandoc is installed. Will convert Markdown to LaTeX after OCR.")
    
    pdf_files = list(INPUT_DIR.glob("*.pdf"))
    if not pdf_files:
        print("No PDFs to process.")
        return
        
    for pdf_file in pdf_files:
        try:
            # Step 1: Convert PDF to Markdown with images
            markdown_path, images_dir = process_pdf(pdf_file)
            
            # Step 2: Convert Markdown to LaTeX if pandoc is available
            if pandoc_available:
                tex_file_path = convert_md_to_latex(markdown_path, images_dir)
                if tex_file_path:
                    print(f"Complete pipeline successful for {pdf_file.name}")
                    print(f"  Markdown: {markdown_path}")
                    print(f"  LaTeX: {tex_file_path}")
            
            # Move processed PDF to done directory
            shutil.move(str(pdf_file), DONE_DIR / pdf_file.name)
            print(f"{pdf_file.name} moved to {DONE_DIR}")
        except Exception as e:
            print(f"Error processing {pdf_file.name}: {e}")


if __name__ == "__main__":
    main()