import learning_apps.adaptive_agent.models
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('main', '0029_p5_external_capability_control'),
        ('adaptive_agent', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='MCPAccessGrant',
            fields=[
                ('grant_id', models.CharField(default=learning_apps.adaptive_agent.models.generate_mcp_grant_id, editable=False, max_length=48, primary_key=True, serialize=False)),
                ('audience', models.CharField(max_length=96)),
                ('token_sha256', models.CharField(max_length=64, unique=True)),
                ('allowed_tools', models.JSONField(default=list)),
                ('expires_at', models.DateTimeField()),
                ('revoked_at', models.DateTimeField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
            ],
            options={
                'db_table': 'agent_mcp_grants_v2',
            },
        ),
        migrations.CreateModel(
            name='PluginRegistration',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('plugin_id', models.CharField(max_length=96)),
                ('version', models.CharField(max_length=32)),
                ('manifest', models.JSONField(default=dict)),
                ('manifest_sha256', models.CharField(max_length=64, unique=True)),
                ('status', models.CharField(choices=[('active', 'active'), ('disabled', 'disabled'), ('retired', 'retired')], default='active', max_length=24)),
                ('installed_at', models.DateTimeField(auto_now_add=True)),
                ('disabled_at', models.DateTimeField(blank=True, null=True)),
            ],
            options={
                'db_table': 'agent_plugins_v2',
                'indexes': [models.Index(fields=['plugin_id', 'status'], name='idx_av2_plugin_status')],
            },
        ),
        migrations.AddConstraint(
            model_name='pluginregistration',
            constraint=models.UniqueConstraint(fields=('plugin_id', 'version'), name='uix_av2_plugin_version'),
        ),
        migrations.AddField(
            model_name='mcpaccessgrant',
            name='conversation',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='agent_mcp_grants', to='main.learningconversation'),
        ),
        migrations.AddField(
            model_name='mcpaccessgrant',
            name='learning_goal',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='agent_mcp_grants', to='main.learninggoal'),
        ),
        migrations.AddField(
            model_name='mcpaccessgrant',
            name='plugin',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='mcp_grants', to='adaptive_agent.pluginregistration'),
        ),
        migrations.AddField(
            model_name='mcpaccessgrant',
            name='user',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='agent_mcp_grants', to='main.userprofile'),
        ),
        migrations.AddIndex(
            model_name='mcpaccessgrant',
            index=models.Index(fields=['audience', 'expires_at'], name='idx_av2_mcp_audience'),
        ),
        migrations.AddIndex(
            model_name='mcpaccessgrant',
            index=models.Index(fields=['user', 'learning_goal', 'revoked_at'], name='idx_av2_mcp_scope'),
        ),
    ]
