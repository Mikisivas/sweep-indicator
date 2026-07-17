# Student Course Management System (COS 201)

A console-based Student Course Management System built in JavaScript (Node.js)
for the COS 201 Programming I mid-semester lab assessment. It lets a student
add courses, view them, search by course code, compute total credit units, and
save/load the course list to a file.

## Requirements

- Node.js 18 or newer (no external packages needed)

## How to run

```bash
cd student-course-management
npm start        # or: node src/app.js
```

Then follow the on-screen menu:

```
1. Add Course
2. View All Courses
3. Search Course by Code
4. Compute Total Units
5. Save to File
6. Load from File
7. Exit Program
```

Saved courses are written to `data/courses.json` (a sample file is included).

## Project structure

| Path | Purpose |
|---|---|
| `src/app.js` | Entry point: menu display, user input, error catching. The menu is **recursive** — it calls itself after each action. |
| `src/Course.js` | `Course` class: holds one course and does all string processing/validation (trimming, casing, format checks). |
| `src/CourseManager.js` | `CourseManager` class: stores the course list and implements add, **recursive search**, total units, save, and load. |
| `src/errors.js` | Custom exception classes (`ValidationError`, `DuplicateCourseError`, `CourseNotFoundError`, `FileOperationError`). |
| `data/courses.json` | Sample saved course list. |
| `screenshots/program-running.png` | Screenshot of the program running. |
| `REPORT.md` | Short project report (submission requirement). |
