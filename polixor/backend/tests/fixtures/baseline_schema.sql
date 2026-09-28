CREATE TABLE sources (
	id VARCHAR(32) NOT NULL, 
	kind VARCHAR(12) NOT NULL, 
	url TEXT NOT NULL, 
	title TEXT NOT NULL, 
	uploader TEXT NOT NULL, 
	file_path TEXT NOT NULL, 
	file_size INTEGER NOT NULL, 
	duration FLOAT NOT NULL, 
	width INTEGER NOT NULL, 
	height INTEGER NOT NULL, 
	fps FLOAT NOT NULL, 
	has_audio BOOLEAN NOT NULL, 
	is_live BOOLEAN NOT NULL, 
	extra JSON NOT NULL, 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (id)
);
CREATE TABLE jobs (
	id VARCHAR(32) NOT NULL, 
	source_id VARCHAR(32), 
	title TEXT NOT NULL, 
	input_url TEXT NOT NULL, 
	status VARCHAR(9) NOT NULL, 
	stage VARCHAR(12) NOT NULL, 
	stage_progress FLOAT NOT NULL, 
	overall_progress FLOAT NOT NULL, 
	message TEXT NOT NULL, 
	error TEXT NOT NULL, 
	error_code VARCHAR(64) NOT NULL, 
	completed_stages JSON NOT NULL, 
	artifacts JSON NOT NULL, 
	settings_snapshot JSON NOT NULL, 
	is_live_mode BOOLEAN NOT NULL, 
	live_cycles INTEGER NOT NULL, 
	live_state VARCHAR(16) NOT NULL, 
	live_started_at DATETIME, 
	live_captured_seconds FLOAT NOT NULL, 
	live_reconnects INTEGER NOT NULL, 
	live_stop_requested BOOLEAN NOT NULL, 
	live_segments JSON NOT NULL, 
	live_error TEXT NOT NULL, 
	created_at DATETIME NOT NULL, 
	started_at DATETIME, 
	finished_at DATETIME, 
	PRIMARY KEY (id), 
	FOREIGN KEY(source_id) REFERENCES sources (id)
);
CREATE TABLE transcript_segments (
	id INTEGER NOT NULL, 
	job_id VARCHAR(32) NOT NULL, 
	idx INTEGER NOT NULL, 
	start FLOAT NOT NULL, 
	"end" FLOAT NOT NULL, 
	text TEXT NOT NULL, 
	language VARCHAR(8) NOT NULL, 
	avg_logprob FLOAT NOT NULL, 
	no_speech_prob FLOAT NOT NULL, 
	words JSON NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(job_id) REFERENCES jobs (id)
);
CREATE INDEX ix_transcript_segments_job_id ON transcript_segments (job_id);
CREATE INDEX ix_segment_job_start ON transcript_segments (job_id, start);
CREATE TABLE moments (
	id VARCHAR(32) NOT NULL, 
	job_id VARCHAR(32) NOT NULL, 
	start FLOAT NOT NULL, 
	"end" FLOAT NOT NULL, 
	peak_time FLOAT NOT NULL, 
	score FLOAT NOT NULL, 
	title TEXT NOT NULL, 
	description TEXT NOT NULL, 
	reason TEXT NOT NULL, 
	category VARCHAR(32) NOT NULL, 
	signals JSON NOT NULL, 
	source_of_truth VARCHAR(16) NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(job_id) REFERENCES jobs (id)
);
CREATE INDEX ix_moments_job_id ON moments (job_id);
CREATE TABLE clips (
	id VARCHAR(32) NOT NULL, 
	job_id VARCHAR(32) NOT NULL, 
	moment_id VARCHAR(32), 
	kind VARCHAR(10) NOT NULL, 
	status VARCHAR(12) NOT NULL, 
	title TEXT NOT NULL, 
	description TEXT NOT NULL, 
	reason TEXT NOT NULL, 
	score FLOAT NOT NULL, 
	source_start FLOAT NOT NULL, 
	source_end FLOAT NOT NULL, 
	duration FLOAT NOT NULL, 
	segments_json JSON NOT NULL, 
	file_path TEXT NOT NULL, 
	file_size INTEGER NOT NULL, 
	thumbnail_path TEXT NOT NULL, 
	width INTEGER NOT NULL, 
	height INTEGER NOT NULL, 
	aspect VARCHAR(16) NOT NULL, 
	layout VARCHAR(16) NOT NULL, 
	subtitles_enabled BOOLEAN NOT NULL, 
	subtitle_style JSON NOT NULL, 
	render_params JSON NOT NULL, 
	error TEXT NOT NULL, 
	created_at DATETIME NOT NULL, 
	updated_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(job_id) REFERENCES jobs (id)
);
CREATE INDEX ix_clips_job_id ON clips (job_id);
CREATE TABLE generated_images (
	id VARCHAR(32) NOT NULL, 
	job_id VARCHAR(32), 
	parent_id VARCHAR(32), 
	prompt TEXT NOT NULL, 
	revised_prompt TEXT NOT NULL, 
	aspect VARCHAR(8) NOT NULL, 
	status VARCHAR(10) NOT NULL, 
	error TEXT NOT NULL, 
	error_code VARCHAR(48) NOT NULL, 
	provider VARCHAR(32) NOT NULL, 
	model VARCHAR(48) NOT NULL, 
	is_ai BOOLEAN NOT NULL, 
	note TEXT NOT NULL, 
	file_path TEXT NOT NULL, 
	thumb_path TEXT NOT NULL, 
	file_size INTEGER NOT NULL, 
	width INTEGER NOT NULL, 
	height INTEGER NOT NULL, 
	meta JSON NOT NULL, 
	created_at DATETIME NOT NULL, 
	updated_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(job_id) REFERENCES jobs (id) ON DELETE SET NULL
);
CREATE INDEX ix_generated_images_job_id ON generated_images (job_id);
CREATE INDEX ix_image_job_created ON generated_images (job_id, created_at);
CREATE TABLE stage_timings (
	id INTEGER NOT NULL, 
	job_id VARCHAR(32) NOT NULL, 
	stage VARCHAR(32) NOT NULL, 
	seconds FLOAT NOT NULL, 
	media_seconds FLOAT NOT NULL, 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(job_id) REFERENCES jobs (id)
);
CREATE INDEX ix_stage_timings_job_id ON stage_timings (job_id);
CREATE TABLE subtitle_cues (
	id INTEGER NOT NULL, 
	clip_id VARCHAR(32) NOT NULL, 
	idx INTEGER NOT NULL, 
	start FLOAT NOT NULL, 
	"end" FLOAT NOT NULL, 
	text TEXT NOT NULL, 
	original_text TEXT NOT NULL, 
	language VARCHAR(8) NOT NULL, 
	words JSON NOT NULL, 
	edited BOOLEAN NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(clip_id) REFERENCES clips (id)
);
CREATE INDEX ix_subtitle_cues_clip_id ON subtitle_cues (clip_id);
CREATE TABLE image_placements (
	id VARCHAR(32) NOT NULL, 
	clip_id VARCHAR(32) NOT NULL, 
	image_id VARCHAR(32) NOT NULL, 
	role VARCHAR(10) NOT NULL, 
	at_time FLOAT NOT NULL, 
	duration FLOAT NOT NULL, 
	opacity FLOAT NOT NULL, 
	scale FLOAT NOT NULL, 
	position VARCHAR(16) NOT NULL, 
	fit VARCHAR(12) NOT NULL, 
	enabled BOOLEAN NOT NULL, 
	idx INTEGER NOT NULL, 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(clip_id) REFERENCES clips (id) ON DELETE CASCADE, 
	FOREIGN KEY(image_id) REFERENCES generated_images (id) ON DELETE CASCADE
);
CREATE INDEX ix_placement_clip_time ON image_placements (clip_id, at_time);
CREATE INDEX ix_image_placements_clip_id ON image_placements (clip_id);
CREATE INDEX ix_image_placements_image_id ON image_placements (image_id);
