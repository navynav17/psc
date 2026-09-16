# PSC Guru MCQ Collector

Apify Actor for collecting MCQs from PSC Guru using an authorized account.

Credentials are supplied as secret Actor input fields and are not hard-coded.

Output is written directly to the Apify Dataset. Each record contains the question, options, source, and source URL. Questions are deduplicated by normalized question text during the run.

The actor does not attempt to bypass authentication or access content unavailable to the authorized account.
