import requests
from requests.exceptions import HTTPError
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

    def raise_for_status(self, response):
        if not response.ok:
            raise HTTPError(response.json().get('message', response.reason), response=response)

    @property
    def projects(self):
        response = self.session.get('https://api.optimizely.com/v2/projects')
        projects = response.json()
        for project in projects:
            yield Project(**project)

    def experiments(self, project_id):
        response = self.session.get(
            'https://api.optimizely.com/v2/experiments',
            params={
                'project_id': project_id,
                'per_page': 100,
            }
        )
        self.raise_for_status(response)
        while True:
            for experiment in response.json():
                yield Experiment(**experiment)

            if 'LINK' in response.headers:
                print(response.headers['LINK'])
            else:
                return

    def experiment(self, experiment_id):
        response = self.session.get(
            'https://api.optimizely.com/v2/experiments/{}'.format(experiment_id),
        )
        self.raise_for_status(response)
        return Experiment(**response.json())

    def update_experiment(self, experiment_id, changes):
        response = self.session.patch(
            'https://api.optimizely.com/v2/experiments/{}'.format(experiment_id),
            json=changes,
        )
        self.raise_for_status(response)


COLLECTION_CLS = 'collection_cls'

def subdocuments(cls):
    return attr.ib(
        convert=lambda docs: [cls(**doc) for doc in docs],
        metadata={
            COLLECTION_CLS: cls,
        }
    )


class OptimizelyDocument():
    @classmethod
    def read_from_disk(cls, root):
        meta = read_meta_file(root)

        for field in attr.fields(cls):
            if COLLECTION_CLS in field.metadata:
                docs = []
                subdir = root / field.name
                if subdir.exists():
                    for dirname in meta.get(field.name):
                        docdir = subdir / dirname
                        if not docdir.is_dir():
                            continue
                        change = field.metadata[COLLECTION_CLS].read_from_disk(docdir)
                        docs.append(as_non_null_dict(change))
                meta[field.name] = docs

        return cls(**meta)

    def write_to_disk(self, root):
        docroot = root / self.dirname
        docroot.mkdir(parents=True, exist_ok=True)

        meta = as_non_null_dict(self)

        for field in attr.fields(self.__class__):
            if COLLECTION_CLS in field.metadata:
                objs = []
                for obj in getattr(self, field.name):
                    obj.write_to_disk(docroot / field.name)
                    objs.append(obj.dirname)
                meta[field.name] = objs

        write_meta_file(docroot, meta)


@attr.s
class Project(OptimizelyDocument):
    name = attr.ib()
    confidence_threshold = attr.ib()
    platform = attr.ib()
    sdks = attr.ib()
    status = attr.ib()
    web_snippet = attr.ib()
    account_id = attr.ib()
    created = attr.ib()
    id = attr.ib()
    is_classic = attr.ib()
    last_modified = attr.ib()
    socket_token = attr.ib(default=None)
    dcp_service_id = attr.ib(default=None)

    @property
    def dirname(self):
        return slugify("{} {}".format(self.name, self.id))


@attr.s
class Change(OptimizelyDocument):
    dependencies = attr.ib()
    id = attr.ib()
    type = attr.ib()
    async = attr.ib(default=None)
    allow_additional_redirect = attr.ib(default=None)
    attributes = attr.ib(default=None)
    config = attr.ib(default=None)
    css = attr.ib(default=None)
    destination = attr.ib(default=None)
    extension_id = attr.ib(default=None)
    name = attr.ib(default=None)
    operator = attr.ib(default=None)
    preserve_parameters = attr.ib(default=None)
    rearrange = attr.ib(default=None)
    selector = attr.ib(default=None)
    src = attr.ib(default=None)
    value = attr.ib(default=None)

    @property
    def dirname(self):
        return slugify(self.id)

    @classmethod
    def read_from_disk(cls, change_dir):
        meta = read_meta_file(change_dir)

        if meta['type'] == 'custom_css':
            with (change_dir / 'value.css').open() as value_file:
                meta['value'] = value_file.read()
        elif meta['type'] == 'custom_code':
            with (change_dir / 'value.js').open() as value_file:
                meta['value'] = value_file.read()
        elif meta['type'] == 'insert_html':
            with (change_dir / 'value.html').open() as value_file:
                meta['value'] = value_file.read()

        return cls(**meta)

    def write_to_disk(self, root):
        change_root = root / self.dirname
        change_root.mkdir(parents=True, exist_ok=True)

        meta = as_non_null_dict(self)

        if self.type == 'custom_css':
            contents = meta.pop('value')
            with (change_root / 'value.css').open('w') as value_file:
                value_file.write(contents)
        elif self.type == 'custom_code':
            contents = meta.pop('value')
            with (change_root / 'value.js').open('w') as value_file:
                value_file.write(contents)
        elif self.type == 'insert_html':
            contents = meta.pop('value')
            with (change_root / 'value.html').open('w') as value_file:
                value_file.write(contents)

        write_meta_file(change_root, meta)


@attr.s
class Action(OptimizelyDocument):
    changes = subdocuments(Change)
    page_id = attr.ib()

    @property
    def dirname(self):
        return slugify(str(self.page_id))



@attr.s
class Variation(OptimizelyDocument):
    weight = attr.ib()
    actions = subdocuments(Action)
    archived = attr.ib()
    variation_id = attr.ib()
    key = attr.ib(default=None)
    name = attr.ib(default=None)

    @property
    def dirname(self):
        return slugify("{} {}".format(self.name, self.variation_id))


@attr.s
class Experiment(OptimizelyDocument):
    project_id = attr.ib()
    variations = subdocuments(Variation)
    changes = subdocuments(Change)
    metrics = attr.ib()
    type = attr.ib()
    created = attr.ib()
    id = attr.ib()
    is_classic = attr.ib()
    last_modified = attr.ib()
    status = attr.ib()
    audience_conditions = attr.ib(default=None)
    earliest = attr.ib(default=None)
    latest = attr.ib(default=None)
    schedule = attr.ib(default=None)
    key = attr.ib(default=None)
    holdback = attr.ib(default=None)
    description = attr.ib(default=None)
    campaign_id = attr.ib(default=None)
    name = attr.ib(default=None)

    @property
    def dirname(self):
        return slugify("{} {}".format(self.name, self.id))


def write_meta_file(root, meta_document):
    meta_file = root / META_FILE

    with meta_file.open('w') as meta_file_handle:
        yaml.safe_dump(
            {k: v for k, v in meta_document.items() if v is not None},
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


def slugify(directory):
    return directory.replace(' ', '_')


def as_non_null_dict(obj):
    return attr.asdict(obj, filter=lambda a, v: v is not None)


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
def pull(ctx, root):
    optimizely = ctx.obj['OPTIMIZELY']
    project_root = Path(root)
    for project in optimizely.projects:
        project.write_to_disk(project_root)

        experiment_root = project_root / project.dirname / 'experiments'
        for experiment in optimizely.experiments(project.id):
            experiment.write_to_disk(experiment_root)


@cli.command('pull-experiment')
@click.argument('experiment', type=click.Path(exists=True, file_okay=False))
@click.pass_context
def pull_experiment(ctx, experiment):
    optimizely = ctx.obj['OPTIMIZELY']

    local = Experiment.read_from_disk(Path(experiment))
    remote = optimizely.experiment(local.id)

    remote.write_to_disk(Path(experiment).parent)


@cli.command('push-experiment')
@click.argument('experiment', type=click.Path(exists=True, file_okay=False))
@click.option('--context-lines', '-n', type=int, default=3)
@click.pass_context
def push_experiment(ctx, experiment, context_lines):
    optimizely = ctx.obj['OPTIMIZELY']

    local = Experiment.read_from_disk(Path(experiment))
    remote = optimizely.experiment(local.id)

    remote_doc = filter_modifiable_experiment_keys(as_non_null_dict(remote))
    local_doc = filter_modifiable_experiment_keys(as_non_null_dict(local))

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
            optimizely.update_experiment(local.id, local_doc)


if __name__ == '__main__':
    cli()
