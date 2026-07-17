# COS 201 Programming I — Mid-Semester Lab Assessment Report

**Project:** Student Course Management System (Mini-System)
**Language:** JavaScript (Node.js, console-based interface)

## 1. What the System Does

The Student Course Management System is a console application that helps a
student record and manage the courses they are taking for the semester. Through
a numbered menu, the user can add a course (code, title, and credit unit), view
all recorded courses in a formatted table, search for a course by its code,
compute the total credit units registered, save the course list to a file, and
load a previously saved list back into the program. The program keeps running,
redisplaying the menu after every action, until the user chooses "Exit
Program".

## 2. How Each Major Feature Works

The program is organised into two classes plus a small set of custom error
types, following an object-oriented design.

- **Add Course** — The user is prompted for a code, title, and unit. These are
  passed to the `Course` class constructor, which performs all string
  processing: the code is trimmed, stripped of inner spaces, uppercased, and
  checked against the pattern *2–4 letters followed by 3 digits* (so
  `" cos 201 "` becomes `COS201`); the title is trimmed and converted to Title
  Case (roman numerals like "II" stay uppercase); the unit must parse to a
  whole number between 1 and 10. `CourseManager.addCourse()` then rejects
  duplicate codes before pushing the new object into its internal array.
- **View All Courses** — `CourseManager.getAll()` returns the stored courses,
  and a loop prints each one as an aligned table row using `padEnd`/`padStart`.
- **Search Course by Code** — The entered code is normalised the same way as
  when adding, then looked up with a **recursive** linear search (described in
  section 4). If nothing matches, a `CourseNotFoundError` is thrown.
- **Compute Total Units** — `CourseManager.totalUnits()` iterates over the
  course array with a `for...of` loop, accumulating the units.
- **Save to File** — The course list is serialised to JSON with
  `JSON.stringify` and written to `data/courses.json` using Node's `fs` module.
- **Load from File** — The file is read and parsed, and every record is passed
  back through the `Course` constructor so that a hand-edited or corrupted file
  cannot inject invalid data into the system.

## 3. Challenges Encountered and How Errors Were Handled

The main challenge was making sure that no user mistake could crash the
program. This was solved with exception handling built on four custom error
classes that extend `Error`: `ValidationError` (empty or badly formatted
input), `DuplicateCourseError` (adding a code that already exists),
`CourseNotFoundError` (searching for a missing course), and
`FileOperationError` (loading a file that does not exist or contains invalid
JSON). The classes *throw* these errors at the point where the problem is
detected, and a single `try...catch` block in the menu loop catches them,
prints a clear message such as `[ValidationError] Invalid unit "abc"...`, and
returns the user to the menu. Invalid menu choices are handled by the
`switch` statement's `default` branch.

A second challenge was reading console input reliably. Node's `readline`
module can drop lines that arrive while no prompt is active, so the program
buffers incoming lines in a queue and each prompt takes the next line from
that queue — which also makes the program behave correctly if input ends
unexpectedly.

## 4. Where Recursion Was Applied

Recursion is used in two places:

1. **The main menu** (`mainMenu()` in `src/app.js`): after an action finishes
   or its error is handled, the function calls itself to display the menu
   again. The base case is menu option 7 (Exit), which returns without the
   recursive call.
2. **Course search** (`findByCode()` in `src/CourseManager.js`): instead of a
   loop, the method examines one array index per call and recurses with
   `index + 1`. Its base cases are reaching the end of the list (returns
   `null`) and finding a matching code (returns the `Course` object).
