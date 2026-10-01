# Infraestrutura AWS

Esta pasta contém a primeira camada da infraestrutura do CloudOps Lab, gerida com Terraform:

- um repositório Amazon ECR para as imagens Docker da API;
- análise de vulnerabilidades das imagens no envio para o ECR;
- um grupo de registos CloudWatch com retenção configurável.

Nesta fase, o Terraform não é aplicado automaticamente e o repositório não cria recursos na AWS. O objetivo é validar a configuração antes de desenhar a rede, o serviço ECS e os controlos de acesso.

## Validação local

É necessário ter Terraform 1.6 ou superior instalado:

```sh
terraform -chdir=infra/terraform init -backend=false
terraform -chdir=infra/terraform fmt -check
terraform -chdir=infra/terraform validate
