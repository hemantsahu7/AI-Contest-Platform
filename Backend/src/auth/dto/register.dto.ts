import { ApiProperty } from '@nestjs/swagger';
import { IsEmail, IsString, MinLength } from 'class-validator';

export class RegisterDto {
  @ApiProperty()
  @IsString()
  username!: string;

  @ApiProperty()
  @IsEmail()
  email!: string;

  @ApiProperty({ minLength: 8 })
  @IsString()
  @MinLength(8)
  password!: string;
  // Deliberately no organizationId: organization membership is granted only by an org admin/instructor
  // (POST /organizations/:id/members). The global ValidationPipe (forbidNonWhitelisted) rejects the field with 400.
}
