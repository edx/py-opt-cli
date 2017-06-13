import requests
import attr
import yaml
import click
import os
from pathlib import Path
import difflib
import pprint


META_FILE = '.meta.yaml'


class Optimizely():
    def __init__(self, token):
        self.session = requests.Session()
        self.session.headers['Authorization'] = "Bearer {}".format(token)

    @property
    def projects(self):
        response = self.session.get('https://api.optimizely.com/v2/projects')
        projects = response.json()
        for project in projects:
            yield Project(project)

    def experiments(self, project_id):
        response = self.session.get(
            'https://api.optimizely.com/v2/experiments',
            params={
                'project_id': project_id,
                'per_page': 100,
            }
        )
        while True:
            for experiment in response.json():
                yield Experiment(experiment)

            if 'LINK' in response.headers:
                print(response.headers['LINK'])
            else:
                return

    def experiment(self, experiment_id):
        return Experiment(self.session.get(
            'https://api.optimizely.com/v2/experiments/{}'.format(experiment_id),
        ).json())

    def update_experiment(self, experiment_id, changes):
        self.session.patch(
            'https://api.optimizely.com/v2/experiments/{}'.format(experiment_id),
            json=changes,
        )


@attr.s
class Project():
    document = attr.ib(convert=dict)

    @property
    def dirname(self):
        return "{} - {}".format(self.document['name'], self.document['id'])

    @classmethod
    def read_from_disk(cls, project_dir):
        meta = read_meta_file(project_dir)

        return cls(meta)

    def write_to_disk(self, root):
        project_root = root / self.dirname
        project_root.mkdir(parents=True, exist_ok=True)

        write_meta_file(project_root, self.document)


@attr.s
class Experiment():
    document = attr.ib(convert=dict)

    @property
    def dirname(self):
        return "{} - {}".format(self.document.get('name'), self.document['id'])

    @classmethod
    def read_from_disk(cls, experiment_dir):
        meta = read_meta_file(experiment_dir)

        changes = []
        changes_dir = experiment_dir / 'changes'
        if changes_dir.exists():
            for dirname in meta['changes']:
                change_dir = changes_dir / dirname
                if not change_dir.is_dir():
                    continue
                change = Change.read_from_disk(change_dir)
                changes.append(change.document)
        meta['changes'] = changes

        variations = []
        variations_dir = experiment_dir / 'variations'
        if variations_dir.exists():
            for dirname in meta['variations']:
                variation_dir = variations_dir / dirname
                if not variation_dir.is_dir():
                    continue
                variation = Variation.read_from_disk(variation_dir)
                variations.append(variation.document)
        meta['variations'] = variations

        return cls(meta)

    def write_to_disk(self, root):
        experiment_root = root / self.dirname
        experiment_root.mkdir(parents=True, exist_ok=True)

        meta = dict(self.document)

        changes = []
        for change in self.global_changes:
            change.write_to_disk(experiment_root / 'changes')
            changes.append(change.dirname)
        meta['changes'] = changes

        variations = []
        for variation in self.variations:
            variation.write_to_disk(experiment_root / 'variations')
            variations.append(variation.dirname)
        meta['variations'] = variations

        write_meta_file(experiment_root, meta)

    @property
    def global_changes(self):
        for change in self.document['changes']:
            yield Change(change)

    @property
    def variations(self):
        for variation in self.document['variations']:
            yield Variation(variation)


@attr.s
class Change():
    document = attr.ib(convert=dict)

    @property
    def dirname(self):
        return self.document['id']

    @classmethod
    def read_from_disk(cls, change_dir):
        meta = read_meta_file(change_dir)

        if meta['type'] == 'custom_css':
            with (change_dir / 'value.css').open() as value_file:
                meta['value'] = value_file.read()
        elif meta['type'] == 'custom_code':
            with (change_dir / 'value.js').open() as value_file:
                meta['value'] = value_file.read()

        return cls(meta)

    def write_to_disk(self, root):
        change_root = root / self.dirname
        change_root.mkdir(parents=True, exist_ok=True)

        meta = dict(self.document)

        if self.document['type'] == 'custom_css':
            contents = meta.pop('value')
            with (change_root / 'value.css').open('w') as value_file:
                value_file.write(contents)
        elif self.document['type'] == 'custom_code':
            contents = meta.pop('value')
            with (change_root / 'value.js').open('w') as value_file:
                value_file.write(contents)

        write_meta_file(change_root, meta)


@attr.s
class Variation():
    document = attr.ib(convert=dict)

    @property
    def dirname(self):
        return "{} - {}".format(self.document.get('name'), self.document['variation_id'])

    @classmethod
    def read_from_disk(cls, variation_dir):
        meta = read_meta_file(variation_dir)

        actions = []
        actions_dir = variation_dir / 'actions'
        if actions_dir.exists():
            for dirname in meta['actions']:
                action_dir = actions_dir / dirname
                if not action_dir.is_dir():
                    continue
                action = Action.read_from_disk(action_dir)
                actions.append(action.document)
        meta['actions'] = actions

        return cls(meta)

    def write_to_disk(self, root):
        variation_root = root / self.dirname
        variation_root.mkdir(parents=True, exist_ok=True)

        meta = dict(self.document)

        actions = []
        for action in self.actions:
            action.write_to_disk(variation_root / 'actions')
            actions.append(action.dirname)
        meta['actions'] = actions

        write_meta_file(variation_root, meta)

    @property
    def actions(self):
        for action in self.document['actions']:
            yield Action(action)


@attr.s
class Action():
    document = attr.ib(convert=dict)

    @property
    def dirname(self):
        return str(self.document['page_id'])

    @classmethod
    def read_from_disk(cls, action_dir):
        meta = read_meta_file(action_dir)

        changes = []
        changes_dir = action_dir / 'changes'
        if changes_dir.exists():
            for dirname in meta.get('changes'):
                change_dir = changes_dir / dirname
                if not change_dir.is_dir():
                    continue
                change = Change.read_from_disk(change_dir)
                changes.append(change.document)
        meta['changes'] = changes

        return cls(meta)

    def write_to_disk(self, root):
        action_root = root / self.dirname
        action_root.mkdir(parents=True, exist_ok=True)

        meta = dict(self.document)

        changes = []
        for change in self.changes:
            change.write_to_disk(action_root / 'changes')
            changes.append(change.dirname)
        meta['changes'] = changes

        write_meta_file(action_root, meta)

    @property
    def changes(self):
        for change in self.document['changes']:
            yield Change(change)


def write_meta_file(root, meta_document):
    meta_file = root / META_FILE

    with meta_file.open('w') as meta_file_handle:
        yaml.safe_dump(
            meta_document,
            stream=meta_file_handle,
            default_flow_style=False,
        )

def read_meta_file(root):
    meta_file = root / META_FILE

    with meta_file.open() as meta_file_handle:
        return yaml.safe_load(meta_file_handle)


def filter_modifiable_experiment_keys(experiment):
    filtered = dict(experiment)
    for key in experiment:
        if key not in (
            'audience_conditions', 'changes', 'description', 'holdback',
            'key', 'metrics', 'name', 'schedule', 'variations',
        ):
            del filtered[key]

    return filtered


@click.group()
@click.password_option('--token', envvar='OPTIMIZELY_TOKEN')
@click.pass_context
def cli(ctx, token):
    if ctx.obj is None:
        ctx.obj = {}

    ctx.obj['OPTIMIZELY'] = Optimizely(token)

@cli.command()
@click.option('--root', default='.', type=click.Path(exists=True, file_okay=False))
@click.pass_context
def pull_all(ctx, root):
    optimizely = ctx.obj['OPTIMIZELY']
    project_root = Path(root)
    for project in optimizely.projects:
        project.write_to_disk(project_root)

        experiment_root = project_root / project.dirname / 'experiments'
        for experiment in optimizely.experiments(project.document['id']):
            experiment.write_to_disk(experiment_root)


@cli.command('pull-experiment')
@click.argument('experiment', type=click.Path(exists=True, file_okay=False))
@click.pass_context
def pull_experiment(ctx, experiment):
    optimizely = ctx.obj['OPTIMIZELY']

    local = Experiment.read_from_disk(Path(experiment))
    remote = optimizely.experiment(local.document['id'])

    remote.write_to_disk(Path(experiment).parent)


@cli.command('push-experiment')
@click.argument('experiment', type=click.Path(exists=True, file_okay=False))
@click.option('--context-lines', '-n', type=int, default=3)
@click.pass_context
def push_experiment(ctx, experiment, context_lines):
    optimizely = ctx.obj['OPTIMIZELY']

    local = Experiment.read_from_disk(Path(experiment))
    remote = optimizely.experiment(local.document['id'])

    remote_doc = filter_modifiable_experiment_keys(remote.document)
    local_doc = filter_modifiable_experiment_keys(local.document)

    if local_doc == remote_doc:
        click.secho("No changes!", fg='green')
    else:
        for diffline in difflib.unified_diff(
            yaml.dump(remote_doc, default_flow_style=False).splitlines(),
            yaml.dump(local_doc, default_flow_style=False).splitlines(),
            fromfile='remote',
            tofile='local',
            n=context_lines,
        ):
            if diffline.startswith(' '):
                click.secho(diffline, fg='white')
            elif diffline.startswith('-'):
                click.secho(diffline, fg='red')
            elif diffline.startswith('+'):
                click.secho(diffline, fg='green')
            elif diffline.startswith('?'):
                click.secho(diffline, fg='yellow')

        if click.confirm('Push these experiment changes?'):
            optimizely.update_experiment(local.document['id'], local_doc)


if __name__ == '__main__':
    cli()
