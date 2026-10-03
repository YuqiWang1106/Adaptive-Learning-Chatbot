import learning_apps.adaptive_agent.models
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ('main', '0029_p5_external_capability_control'),
    ]

    operations = [
        migrations.CreateModel(
            name='AgentInterruption',
            fields=[
                ('interruption_id', models.CharField(default=learning_apps.adaptive_agent.models.generate_interruption_id, editable=False, max_length=48, primary_key=True, serialize=False)),
                ('kind', models.CharField(choices=[('clarification', 'clarification'), ('approval', 'approval')], max_length=24)),
                ('status', models.CharField(choices=[('pending', 'pending'), ('answered', 'answered'), ('approved', 'approved'), ('rejected', 'rejected'), ('expired', 'expired'), ('cancelled', 'cancelled')], default='pending', max_length=24)),
                ('tool_name', models.CharField(blank=True, default='', max_length=96)),
                ('sdk_call_id', models.CharField(blank=True, default='', max_length=128)),
                ('public_payload', models.JSONField(default=dict)),
                ('encrypted_response', models.TextField(blank=True, default='')),
                ('response_sha256', models.CharField(blank=True, default='', max_length=64)),
                ('idempotency_key', models.CharField(max_length=96)),
                ('expires_at', models.DateTimeField()),
                ('resolved_at', models.DateTimeField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
            ],
            options={
                'db_table': 'agent_interruptions_v2',
            },
        ),
        migrations.CreateModel(
            name='AgentReleaseManifest',
            fields=[
                ('release_id', models.CharField(default=learning_apps.adaptive_agent.models.generate_release_id, editable=False, max_length=48, primary_key=True, serialize=False)),
                ('release_name', models.CharField(max_length=96)),
                ('model', models.CharField(max_length=96)),
                ('reasoning_effort', models.CharField(default='medium', max_length=16)),
                ('prompt_version', models.CharField(max_length=48)),
                ('runtime_version', models.CharField(max_length=48)),
                ('capability_catalog_version', models.CharField(max_length=48)),
                ('manifest', models.JSONField(default=dict)),
                ('manifest_sha256', models.CharField(max_length=64, unique=True)),
                ('active', models.BooleanField(default=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
            ],
            options={
                'db_table': 'agent_v2_release_manifests',
            },
        ),
        migrations.CreateModel(
            name='OutboxEvent',
            fields=[
                ('event_id', models.CharField(default=learning_apps.adaptive_agent.models.generate_outbox_id, editable=False, max_length=48, primary_key=True, serialize=False)),
                ('aggregate_type', models.CharField(max_length=48)),
                ('aggregate_id', models.CharField(max_length=64)),
                ('event_type', models.CharField(max_length=96)),
                ('payload', models.JSONField(default=dict)),
                ('status', models.CharField(choices=[('pending', 'pending'), ('published', 'published'), ('failed', 'failed')], default='pending', max_length=24)),
                ('publish_attempts', models.PositiveSmallIntegerField(default=0)),
                ('available_at', models.DateTimeField()),
                ('published_at', models.DateTimeField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
            ],
            options={
                'db_table': 'outbox_events_v2',
                'indexes': [models.Index(fields=['status', 'available_at'], name='idx_outbox_status_due')],
            },
        ),
        migrations.CreateModel(
            name='LearningAgentRun',
            fields=[
                ('run_id', models.CharField(default=learning_apps.adaptive_agent.models.generate_run_id, editable=False, max_length=48, primary_key=True, serialize=False)),
                ('conversation_generation', models.PositiveIntegerField()),
                ('status', models.CharField(choices=[('queued', 'queued'), ('running', 'running'), ('waiting_for_clarification', 'waiting_for_clarification'), ('waiting_for_approval', 'waiting_for_approval'), ('resuming', 'resuming'), ('completed', 'completed'), ('failed', 'failed'), ('cancelled', 'cancelled'), ('expired', 'expired')], default='queued', max_length=40)),
                ('idempotency_key', models.CharField(max_length=96)),
                ('request_sha256', models.CharField(max_length=64)),
                ('trace_id', models.CharField(db_index=True, max_length=64)),
                ('model', models.CharField(max_length=96)),
                ('reasoning_effort', models.CharField(default='medium', max_length=16)),
                ('max_turns', models.PositiveSmallIntegerField(default=4)),
                ('max_tool_calls', models.PositiveSmallIntegerField(default=6)),
                ('max_skills', models.PositiveSmallIntegerField(default=2)),
                ('turn_count', models.PositiveSmallIntegerField(default=0)),
                ('tool_call_count', models.PositiveSmallIntegerField(default=0)),
                ('skill_count', models.PositiveSmallIntegerField(default=0)),
                ('plan', models.JSONField(blank=True, default=list)),
                ('selected_skills', models.JSONField(blank=True, default=list)),
                ('evidence_manifest', models.JSONField(blank=True, default=list)),
                ('result_payload', models.JSONField(blank=True, default=dict)),
                ('error_code', models.CharField(blank=True, default='', max_length=64)),
                ('cancel_requested_at', models.DateTimeField(blank=True, null=True)),
                ('started_at', models.DateTimeField(blank=True, null=True)),
                ('completed_at', models.DateTimeField(blank=True, null=True)),
                ('expires_at', models.DateTimeField()),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('mastery_write_authorized', models.BooleanField(default=False, editable=False)),
                ('conversation', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='learning_agent_runs', to='main.learningconversation')),
                ('learning_goal', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='learning_agent_runs', to='main.learninggoal')),
                ('release', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='runs', to='adaptive_agent.agentreleasemanifest')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='learning_agent_runs', to='main.userprofile')),
            ],
            options={
                'db_table': 'learning_agent_runs_v2',
            },
        ),
        migrations.CreateModel(
            name='ExecutionRun',
            fields=[
                ('execution_id', models.CharField(default=learning_apps.adaptive_agent.models.generate_execution_id, editable=False, max_length=48, primary_key=True, serialize=False)),
                ('entrypoint', models.CharField(max_length=24)),
                ('workflow', models.CharField(max_length=96)),
                ('trace_id', models.CharField(db_index=True, max_length=64)),
                ('status', models.CharField(choices=[('running', 'running'), ('completed', 'completed'), ('failed', 'failed')], default='running', max_length=24)),
                ('started_at', models.DateTimeField(auto_now_add=True)),
                ('completed_at', models.DateTimeField(blank=True, null=True)),
                ('agent_run', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='executions', to='adaptive_agent.learningagentrun')),
                ('conversation', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='capability_executions', to='main.learningconversation')),
                ('learning_goal', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='capability_executions', to='main.learninggoal')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='capability_executions', to='main.userprofile')),
            ],
            options={
                'db_table': 'capability_execution_runs_v2',
            },
        ),
        migrations.CreateModel(
            name='CapabilityInvocation',
            fields=[
                ('invocation_id', models.CharField(default=learning_apps.adaptive_agent.models.generate_invocation_id, editable=False, max_length=48, primary_key=True, serialize=False)),
                ('capability_name', models.CharField(max_length=96)),
                ('capability_version', models.CharField(max_length=32)),
                ('authority', models.CharField(max_length=24)),
                ('status', models.CharField(choices=[('succeeded', 'succeeded'), ('blocked', 'blocked'), ('failed', 'failed')], max_length=24)),
                ('idempotency_key', models.CharField(max_length=96)),
                ('input_sha256', models.CharField(max_length=64)),
                ('output_sha256', models.CharField(max_length=64)),
                ('reason_code', models.CharField(blank=True, default='', max_length=64)),
                ('latency_ms', models.PositiveIntegerField(default=0)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('mastery_write_authorized', models.BooleanField(default=False, editable=False)),
                ('execution', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='invocations', to='adaptive_agent.executionrun')),
            ],
            options={
                'db_table': 'capability_invocations_v2',
            },
        ),
        migrations.CreateModel(
            name='AgentRunEvent',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('sequence', models.PositiveIntegerField()),
                ('event_type', models.CharField(choices=[('run_started', 'run_started'), ('plan_updated', 'plan_updated'), ('skill_selected', 'skill_selected'), ('tool_requested', 'tool_requested'), ('tool_started', 'tool_started'), ('tool_completed', 'tool_completed'), ('evidence_attached', 'evidence_attached'), ('clarification_requested', 'clarification_requested'), ('approval_requested', 'approval_requested'), ('approval_resolved', 'approval_resolved'), ('answer_streaming', 'answer_streaming'), ('run_completed', 'run_completed'), ('run_failed', 'run_failed')], max_length=40)),
                ('public_payload', models.JSONField(blank=True, default=dict)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('run', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='events', to='adaptive_agent.learningagentrun')),
            ],
            options={
                'db_table': 'agent_run_events_v2',
            },
        ),
        migrations.CreateModel(
            name='AgentRunCheckpoint',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('encrypted_state', models.TextField()),
                ('state_sha256', models.CharField(max_length=64)),
                ('sdk_schema_version', models.CharField(default='1', max_length=24)),
                ('expires_at', models.DateTimeField()),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('run', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='checkpoint', to='adaptive_agent.learningagentrun')),
            ],
            options={
                'db_table': 'agent_run_checkpoints_v2',
            },
        ),
        migrations.AddIndex(
            model_name='agentreleasemanifest',
            index=models.Index(fields=['active', 'created_at'], name='idx_av2_release_active'),
        ),
        migrations.AddField(
            model_name='agentinterruption',
            name='run',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='interruptions', to='adaptive_agent.learningagentrun'),
        ),
        migrations.AddIndex(
            model_name='learningagentrun',
            index=models.Index(fields=['user', 'learning_goal', 'created_at'], name='idx_av2_run_scope'),
        ),
        migrations.AddIndex(
            model_name='learningagentrun',
            index=models.Index(fields=['status', 'expires_at'], name='idx_av2_run_status'),
        ),
        migrations.AddConstraint(
            model_name='learningagentrun',
            constraint=models.UniqueConstraint(fields=('user', 'learning_goal', 'idempotency_key'), name='uix_av2_run_scope_idem'),
        ),
        migrations.AddConstraint(
            model_name='learningagentrun',
            constraint=models.CheckConstraint(check=models.Q(('mastery_write_authorized', False)), name='chk_av2_run_no_mastery'),
        ),
        migrations.AddIndex(
            model_name='executionrun',
            index=models.Index(fields=['user', 'learning_goal', 'started_at'], name='idx_cap_exec_scope'),
        ),
        migrations.AddIndex(
            model_name='capabilityinvocation',
            index=models.Index(fields=['capability_name', 'status', 'created_at'], name='idx_cap_inv_status'),
        ),
        migrations.AddConstraint(
            model_name='capabilityinvocation',
            constraint=models.UniqueConstraint(fields=('execution', 'capability_name', 'idempotency_key'), name='uix_cap_invocation_idem'),
        ),
        migrations.AddConstraint(
            model_name='capabilityinvocation',
            constraint=models.CheckConstraint(check=models.Q(('mastery_write_authorized', False)), name='chk_cap_inv_no_mastery'),
        ),
        migrations.AddIndex(
            model_name='agentrunevent',
            index=models.Index(fields=['run', 'sequence'], name='idx_av2_event_run_seq'),
        ),
        migrations.AddConstraint(
            model_name='agentrunevent',
            constraint=models.UniqueConstraint(fields=('run', 'sequence'), name='uix_av2_event_sequence'),
        ),
        migrations.AddIndex(
            model_name='agentruncheckpoint',
            index=models.Index(fields=['expires_at'], name='idx_av2_checkpoint_exp'),
        ),
        migrations.AddIndex(
            model_name='agentinterruption',
            index=models.Index(fields=['run', 'status', 'expires_at'], name='idx_av2_interrupt_run'),
        ),
        migrations.AddConstraint(
            model_name='agentinterruption',
            constraint=models.UniqueConstraint(fields=('run', 'idempotency_key'), name='uix_av2_interrupt_idem'),
        ),
    ]
