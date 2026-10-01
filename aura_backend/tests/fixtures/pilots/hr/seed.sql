-- Deterministic seed for the "hr" pilot: no random(), no now() in values, so a profile of this
-- data is the same on every machine (the golden catalog depends on it).

INSERT INTO departments (name, cost_center)
SELECT d, 'CC-' || lpad(i::text, 3, '0')
  FROM unnest(ARRAY['Engineering', 'People', 'Finance', 'Operations']) WITH ORDINALITY AS t(d, i);

INSERT INTO employees (employee_code, full_name, email, department_id, hired_on, status, salary)
SELECT 'E' || lpad(g::text, 4, '0'),
       'Employee ' || g,
       'employee' || g || '@hr.example',
       1 + g % 4,
       date '2020-01-06' + g * 17,
       (ARRAY['active', 'active', 'active', 'on_leave', 'terminated'])[1 + g % 5],
       42000 + g * 750
  FROM generate_series(1, 30) AS g;

UPDATE employees SET manager_id = 1 + id % 4 WHERE id > 4;

INSERT INTO leave_requests (employee_id, leave_type, starts_on, ends_on, status, approved_by, requested_at)
SELECT 1 + g % 30,
       (ARRAY['annual', 'sick', 'unpaid', 'parental'])[1 + g % 4]::leave_type,
       date '2026-01-05' + g * 3,
       date '2026-01-05' + g * 3 + (g % 5),
       (ARRAY['pending', 'approved', 'approved', 'rejected', 'cancelled'])[1 + g % 5],
       CASE WHEN g % 5 IN (1, 2) THEN 1 + g % 4 END,
       timestamptz '2025-12-01 09:00:00+00' + g * interval '1 day'
  FROM generate_series(1, 40) AS g;

INSERT INTO attendance (employee_id, work_date, check_in, check_out)
SELECT 1 + g % 20,
       date '2026-03-02' + g / 20,
       timestamptz '2026-03-02 08:30:00+00' + (g / 20) * interval '1 day',
       timestamptz '2026-03-02 17:00:00+00' + (g / 20) * interval '1 day'
  FROM generate_series(0, 59) AS g;

INSERT INTO assets (tag, kind, assigned_to)
SELECT 'AST-' || lpad(g::text, 4, '0'),
       (ARRAY['laptop', 'monitor', 'phone'])[1 + g % 3],
       CASE WHEN g % 4 <> 0 THEN g END
  FROM generate_series(1, 12) AS g;
