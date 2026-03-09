import json
import os
import re
import sys


def parse_pdf_extracted_text(filepath):
    """Parse the extracted PDF text to get questions with their options."""
    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()

    # Remove page markers
    content = re.sub(r"--- PAGE \d+ ---\n?", "", content)

    # Split by "Question #N" pattern
    question_splits = re.split(r"(?:Topic \d+\s*\n\s*)?Question #(\d+)", content)

    questions = {}

    # question_splits: ['preamble', '1', 'question_body', '2', 'question_body', ...]
    i = 1
    while i < len(question_splits) - 1:
        q_num = int(question_splits[i].strip())
        q_body = question_splits[i + 1].strip()
        i += 2

        # Find where options start - look for the first "A." at the start of a line
        option_match = re.search(r"\n([A-F])\.\s", q_body)

        if option_match:
            question_text = q_body[: option_match.start()].strip()
            options_text = q_body[option_match.start() :].strip()
        else:
            question_text = q_body
            options_text = ""

        # Parse individual options
        options = {}
        if options_text:
            # Split options by letter markers at beginning of lines
            option_parts = re.split(r"\n(?=[A-F]\.\s)", options_text)
            for part in option_parts:
                part = part.strip()
                opt_match = re.match(r"^([A-F])\.\s+(.*)", part, re.DOTALL)
                if opt_match:
                    letter = opt_match.group(1)
                    text = opt_match.group(2).strip()
                    # Clean up multi-line option text
                    text = re.sub(r"\s*\n\s*", " ", text)
                    # Remove trailing "Topic 1" or similar artifacts
                    text = re.sub(r"\s*Topic \d+\s*$", "", text)
                    options[letter] = text

        # Clean up question text
        question_text = re.sub(r"\s*\n\s*", " ", question_text)
        question_text = question_text.strip()

        # Detect multi-select questions
        choose_match = re.search(
            r"\((?:Choose|Select)\s+(\w+)\.?\)", question_text, re.IGNORECASE
        )
        num_choices = 1
        if choose_match:
            word = choose_match.group(1).lower()
            word_map = {
                "two": 2,
                "three": 3,
                "four": 4,
                "five": 5,
                "2": 2,
                "3": 3,
                "4": 4,
            }
            num_choices = word_map.get(word, 1)

        questions[q_num] = {
            "number": q_num,
            "question": question_text,
            "options": options,
            "num_choices": num_choices,
        }

    return questions


def normalize_text(text):
    """Normalize text for fuzzy matching."""
    text = text.lower().strip()
    text = re.sub(r"[^a-z0-9\s]", "", text)
    text = re.sub(r"\s+", " ", text)
    return text


def match_answer_text_to_option(answer_text, options):
    """Try to match answer text to one of the option texts. Returns list of matching letters."""
    if not answer_text or not options:
        return []

    answer_norm = normalize_text(answer_text)
    if len(answer_norm) < 10:
        return []

    best_letter = None
    best_score = 0

    for letter, opt_text in options.items():
        opt_norm = normalize_text(opt_text)

        # Check if answer text is contained in option or vice versa
        if answer_norm in opt_norm or opt_norm in answer_norm:
            score = min(len(answer_norm), len(opt_norm))
            if score > best_score:
                best_score = score
                best_letter = letter
            continue

        # Check significant word overlap
        answer_words = set(answer_norm.split())
        opt_words = set(opt_norm.split())

        # Remove common stop words
        stop_words = {
            "the",
            "a",
            "an",
            "to",
            "in",
            "on",
            "of",
            "for",
            "and",
            "or",
            "is",
            "are",
            "with",
            "that",
            "use",
            "as",
            "from",
            "by",
            "be",
            "this",
            "it",
        }
        answer_sig = answer_words - stop_words
        opt_sig = opt_words - stop_words

        if answer_sig and opt_sig:
            overlap = len(answer_sig & opt_sig)
            total = min(len(answer_sig), len(opt_sig))
            if total > 0:
                ratio = overlap / total
                if ratio > 0.5 and overlap > best_score:
                    best_score = overlap
                    best_letter = letter

        # Check if first N significant words match
        answer_first = " ".join(answer_norm.split()[:8])
        opt_first = " ".join(opt_norm.split()[:8])
        if len(answer_first) > 20 and answer_first == opt_first:
            return [letter]

    if best_letter and best_score >= 3:
        return [best_letter]
    return []


def find_all_question_positions(content):
    """Find all question marker positions in the solution file (both N] and N. formats)."""
    markers = []
    # Pattern 1: N] format (e.g., "36]")
    for m in re.finditer(r"(?:^|\n)(\d+)\]", content):
        markers.append((m.start(), int(m.group(1)), m.end()))
    # Pattern 2: N. format followed by uppercase letter (e.g., "51.A" or "51. A")
    for m in re.finditer(r"(?:^|\n)(\d+)\.\s*[A-Z]", content):
        num = int(m.group(1))
        # Only consider numbers that aren't already found as bracket markers
        # and are plausible question numbers (> 0)
        if num > 0:
            markers.append((m.start(), num, m.end()))
    # Sort by position
    markers.sort(key=lambda x: x[0])
    # Deduplicate: if same question number appears multiple times, keep first
    seen = set()
    unique = []
    for pos, num, end in markers:
        if num not in seen:
            seen.add(num)
            unique.append((pos, num, end))
    return unique


# Module-level cache for question positions
_cached_positions = None
_cached_content_id = None


def extract_solution_body(q_num, content):
    """Extract the full body text for a given question number from the solutions file."""
    global _cached_positions, _cached_content_id

    content_id = id(content)
    if _cached_positions is None or _cached_content_id != content_id:
        _cached_positions = find_all_question_positions(content)
        _cached_content_id = content_id

    markers = _cached_positions

    # Find the marker for this question number
    target_idx = None
    for i, (pos, num, end) in enumerate(markers):
        if num == q_num:
            target_idx = i
            break

    if target_idx is None:
        return ""

    start_pos = markers[target_idx][2]  # end of the marker match
    # End at the next marker's start, or end of content
    if target_idx + 1 < len(markers):
        end_pos = markers[target_idx + 1][0]
    else:
        end_pos = len(content)

    body = content[start_pos:end_pos].strip()
    return body


def extract_answers_from_body(body, options=None):
    """Extract correct answers and explanation from a solution body text."""
    if not body:
        return [], ""

    correct_answers = []
    explanation_lines = []

    lines = body.split("\n")

    # Pre-check: detect if this is a multi-select question from the body text
    is_multi_select = bool(
        re.search(
            r"\((?:Choose|Select)\s+(?:two|three|four|2|3|4)\b", body, re.IGNORECASE
        )
    )

    # Strategy 0: Body starts directly with ". Letter answer" (e.g., Q96 format: ". Users can terminate...")
    # This means the body is just the answer text, starting with ". "
    stripped_body = body.strip()
    if stripped_body.startswith(". "):
        # The question number marker captured the leading text; body is just answer text
        # Try to match to options
        answer_text = stripped_body[2:].strip()
        if options:
            matched = match_answer_text_to_option(answer_text, options)
            if matched:
                return matched, answer_text

    # Strategy 0b: Body starts directly with a letter+period answer like "C. Configure AWS WAF..."
    m_direct_start = re.match(r"^([A-F])\.\s+(.+)", stripped_body)
    if m_direct_start and not re.search(r"\?", stripped_body[:200]):
        # Entire body is just an answer line (no question text with ?)
        correct_answers = [m_direct_start.group(1).upper()]
        explanation = stripped_body[m_direct_start.end() :].strip()
        explanation = re.sub(r"\s+", " ", explanation)
        explanation = re.sub(r"[\-=*]{3,}\s*$", "", explanation).strip()
        return correct_answers, explanation

    # Strategy 1: Look for explicit answer patterns with letter
    for line in lines:
        ls = line.strip()

        # "ans- A, B, C" triple (check first, most specific)
        m = re.match(
            r"ans[\s\-:]+\s*([A-F])\s*[,&]\s*([A-F])\s*[,&]\s*([A-F])",
            ls,
            re.IGNORECASE,
        )
        if m:
            correct_answers = [
                m.group(1).upper(),
                m.group(2).upper(),
                m.group(3).upper(),
            ]
            continue

        # "ans- A and B" or "ans- A, B"
        m = re.match(
            r"ans[\s\-:]+\s*([A-F])\s*(?:,|and|&)\s*([A-F])",
            ls,
            re.IGNORECASE,
        )
        if m:
            correct_answers = [m.group(1).upper(), m.group(2).upper()]
            continue

        # "ans- A. ..." or "ans-A" or "ans: A" or "ans - A"
        m = re.match(r"ans[\s\-:]+\s*([A-F])[\.\s,\)]", ls, re.IGNORECASE)
        if m:
            letter = m.group(1).upper()
            if letter not in correct_answers:
                correct_answers.append(letter)
            continue

        # "Answer: A)" or "Answer: A." or "Answer: A"
        m = re.match(r"Answer[\s:]+\s*([A-F])[\.\)\s,]?", ls, re.IGNORECASE)
        if m:
            letter = m.group(1).upper()
            if letter not in correct_answers:
                correct_answers.append(letter)
            # Check for additional letters like "A, B" or "A) and B)"
            rest = ls[m.end() :]
            extra = re.findall(r"\b([A-F])\b", rest[:30])
            for e in extra:
                if e.upper() not in correct_answers:
                    correct_answers.append(e.upper())
            continue

        # "Answer: A) description" pattern
        m = re.match(r"Answer:\s*([A-F])\)", ls, re.IGNORECASE)
        if m:
            letter = m.group(1).upper()
            if letter not in correct_answers:
                correct_answers = [letter]
            continue

        # "Correct answer A" or "Correct answer: A"
        m = re.match(r"Correct answer\s*[:\s]*([A-F])", ls, re.IGNORECASE)
        if m:
            correct_answers = [m.group(1).upper()]
            continue

    # Strategy 1b: For multi-select, also scan for multiple "ans-" lines
    if is_multi_select and len(correct_answers) < 2:
        extra_answers = []
        for line in lines:
            ls = line.strip()
            m = re.match(r"ans[\s\-:]+\s*([A-F])[\.\s,\)]", ls, re.IGNORECASE)
            if m:
                letter = m.group(1).upper()
                if letter not in extra_answers:
                    extra_answers.append(letter)
        if len(extra_answers) > len(correct_answers):
            correct_answers = extra_answers

    # Strategy 2: If no letter found, look for "ans-" followed by text and match to options
    if not correct_answers:
        for line in lines:
            ls = line.strip()
            m = re.match(r"ans[\s\-:]+\s*(.+)", ls, re.IGNORECASE)
            if m:
                answer_text = m.group(1).strip()
                # Check if it starts with a letter pattern we missed
                lm = re.match(r"([A-F])[\.\)\s]", answer_text)
                if lm:
                    correct_answers = [lm.group(1).upper()]
                elif options:
                    matched = match_answer_text_to_option(answer_text, options)
                    if matched:
                        correct_answers = matched
                break

    # Strategy 3: Look for standalone answer lines "A. Create...", "B. Configure..." after question text
    # For multi-select questions, collect ALL such lines
    if not correct_answers:
        in_question = True
        candidate_answers = []
        for idx, line in enumerate(lines):
            ls = line.strip()
            # Question text usually ends with "?" or "requirements" or "(Choose X.)" or similar
            if re.search(
                r"\?$|\?\s*\(Choose \w+\.?\)$|requirements\??$|overhead\??$|cost[- ]effectively\??$|\(Choose \w+\.?\)$",
                ls,
                re.IGNORECASE,
            ):
                in_question = False
                continue
            if ls == "":
                continue
            if not in_question:
                # Look for answer option lines like "A. Create..."
                m = re.match(r"^([A-F])[\.\)]\s+(.+)", ls)
                if m:
                    letter = m.group(1).upper()
                    if letter not in candidate_answers:
                        candidate_answers.append(letter)
                    continue
                # If we already found some answers and hit a non-option line, stop
                if candidate_answers:
                    break
                # Could also be just answer text without letter
                if options and len(ls) > 20:
                    matched = match_answer_text_to_option(ls, options)
                    if matched:
                        candidate_answers = matched
                        break
                break
        if candidate_answers:
            correct_answers = candidate_answers

    # Strategy 4: Look for "Option X:" explanation pattern
    if not correct_answers:
        m = re.search(r"Option\s+([A-F])\s*:", body, re.IGNORECASE)
        if m:
            correct_answers = [m.group(1).upper()]

    # Strategy 5: Search for any "X. <text>" lines that match option texts
    # Collect ALL matching lines for multi-select support
    if not correct_answers and options:
        matched_letters = []
        for line in lines:
            ls = line.strip()
            m = re.match(r"^([A-F])[\.\)]\s+(.+)", ls)
            if m:
                letter = m.group(1).upper()
                text = m.group(2).strip()
                if letter in options:
                    # Verify it matches the option
                    opt_norm = normalize_text(options[letter])
                    text_norm = normalize_text(text)
                    if len(text_norm) > 15 and (
                        text_norm[:40] in opt_norm or opt_norm[:40] in text_norm
                    ):
                        if letter not in matched_letters:
                            matched_letters.append(letter)
        if matched_letters:
            correct_answers = matched_letters

    # Strategy 6: If still nothing, try matching any substantial non-question line to options
    if not correct_answers and options:
        in_question = True
        for line in lines:
            ls = line.strip()
            if not ls:
                continue
            # Skip question text lines (typically the first few lines)
            if in_question:
                if re.search(
                    r"\?$|\?\s*\(Choose \w+\.?\)$|\(Choose \w+\.?\)$|requirements|overhead|solution|meet these",
                    ls,
                    re.IGNORECASE,
                ):
                    in_question = False
                continue
            # Skip separator lines
            if re.match(r"^[-=*]{3,}$", ls):
                continue
            # Try to match this line to options
            if len(ls) > 15:
                # Remove leading markers
                clean = re.sub(r"^[\s\-\*•]+", "", ls)
                matched = match_answer_text_to_option(clean, options)
                if matched:
                    correct_answers = matched
                    break

    # Strategy 7: Look for "is correct" or "is the correct" or "is the best" pattern referencing a letter
    if not correct_answers:
        m = re.search(
            r"\b([A-F])\b\s+is\s+(?:the\s+)?(?:correct|best|right|most appropriate)",
            body,
            re.IGNORECASE,
        )
        if m:
            correct_answers = [m.group(1).upper()]

    # Strategy 8: Look for "the answer is X" pattern
    if not correct_answers:
        m = re.search(r"the answer is\s+([A-F])\b", body, re.IGNORECASE)
        if m:
            correct_answers = [m.group(1).upper()]

    # Extract explanation - everything after the answer declaration
    found_answer = False
    for line in lines:
        ls = line.strip()
        if found_answer:
            if ls and not re.match(r"^[-=*]{3,}$", ls):
                explanation_lines.append(ls)
        elif re.search(r"ans[\s\-:]|^Answer|Correct answer", ls, re.IGNORECASE):
            found_answer = True
            continue
        elif correct_answers and re.match(
            rf"^{re.escape(correct_answers[0])}[\.\)\s]", ls
        ):
            found_answer = True
            # The answer line itself might contain useful info
            continue

    # If no explicit answer line found, treat everything after question as explanation
    if not found_answer:
        in_question = True
        for line in lines:
            ls = line.strip()
            if in_question:
                if re.search(r"\?$|requirements|overhead", ls, re.IGNORECASE):
                    in_question = False
                continue
            if ls and not re.match(r"^[-=*]{3,}$", ls):
                explanation_lines.append(ls)

    explanation = " ".join(explanation_lines).strip()
    explanation = re.sub(r"\s+", " ", explanation)
    explanation = re.sub(r"[\-=*]{3,}\s*$", "", explanation).strip()

    # Deduplicate answers
    seen = set()
    unique_answers = []
    for a in correct_answers:
        if a not in seen:
            seen.add(a)
            unique_answers.append(a)

    return unique_answers, explanation


def parse_solution_file(filepath):
    """Parse the solution file to extract correct answers and explanations."""
    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()
    return content


def merge_questions_and_solutions(pdf_questions, solutions_content):
    """Merge PDF questions with solution answers and explanations."""
    merged = []

    for q_num in sorted(pdf_questions.keys()):
        q = pdf_questions[q_num]

        body = extract_solution_body(q_num, solutions_content)
        correct_answers, explanation = extract_answers_from_body(body, q["options"])

        # Validate answers against available options
        valid_options = list(q["options"].keys())
        if valid_options and correct_answers:
            correct_answers = [a for a in correct_answers if a in valid_options]

        # For multi-select, ensure we have the right number of answers
        if q["num_choices"] > 1 and len(correct_answers) > q["num_choices"]:
            correct_answers = correct_answers[: q["num_choices"]]

        entry = {
            "id": q_num,
            "question": q["question"],
            "options": q["options"],
            "correctAnswers": correct_answers,
            "explanation": explanation,
            "numChoices": q["num_choices"],
        }

        merged.append(entry)

    return merged


def main():
    pdf_extracted_path = (
        "AWS Certified Solutions Architect Associate SAA-C03_extracted.txt"
    )
    solution_path = "AWS SAA-03 Solution.txt"

    if not os.path.exists(pdf_extracted_path):
        print(
            f"Extracted PDF text not found at '{pdf_extracted_path}'. Running extraction first..."
        )
        import subprocess

        subprocess.run([sys.executable, "extract_pdf.py"], check=True)

    print("Parsing PDF extracted text...")
    pdf_questions = parse_pdf_extracted_text(pdf_extracted_path)
    print(f"  Found {len(pdf_questions)} questions from PDF")

    print("Parsing solution file...")
    solutions_content = parse_solution_file(solution_path)
    print(f"  Loaded solutions file")

    print("Merging questions and solutions...")
    merged = merge_questions_and_solutions(pdf_questions, solutions_content)

    # Stats
    with_answers = sum(1 for q in merged if q["correctAnswers"])
    without_answers = sum(1 for q in merged if not q["correctAnswers"])
    multi_select = sum(1 for q in merged if q["numChoices"] > 1)

    print(f"\nResults:")
    print(f"  Total questions: {len(merged)}")
    print(f"  Questions with answers: {with_answers}")
    print(f"  Questions without answers: {without_answers}")
    print(f"  Multi-select questions: {multi_select}")

    if without_answers > 0:
        missing = [q["id"] for q in merged if not q["correctAnswers"]]
        print(
            f"  Missing answer IDs (first 30): {missing[:30]}{'...' if len(missing) > 30 else ''}"
        )

    output_path = "questions.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False, indent=2)

    print(f"\nSaved to '{output_path}'")

    # Also output a compact JS version for embedding
    js_output_path = "questions.js"
    with open(js_output_path, "w", encoding="utf-8") as f:
        f.write("const QUESTION_BANK = ")
        json.dump(merged, f, ensure_ascii=False)
        f.write(";\n")

    print(f"Saved JS version to '{js_output_path}'")


if __name__ == "__main__":
    main()
